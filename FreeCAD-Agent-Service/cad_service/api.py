"""Stateless MCP transport over a durable application job queue."""
from contextlib import asynccontextmanager
from contextvars import ContextVar
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
import uuid

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse, FileResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from cad_service.policy import validate
from cad_service import __version__
from cad_service.queue import Queue, RETAIN_SUCCEEDED
from cad_service.management import Management
from cad_service.admin_api import AdminAPI

agent_context = ContextVar('agent', default=None)
FILES = {'source.py', 'model.FCStd', 'model.step', 'model.stl', 'report.json'}


def expired_problem(row):
    limit = row.get('retention_limit_at_expiry') or RETAIN_SUCCEEDED
    message = '管理员已删除该任务产物。' if row.get('artifacts_expiry_reason') == 'manual' else f'该任务产物已按保留策略删除；当时仅保留最近 {limit} 个成功任务的产物。'
    return {'job_id': row['id'], 'files': {}, 'error': {
        'code': 'ARTIFACTS_EXPIRED',
        'message': message,
        'action': '请使用新的 request_id 重新提交建模，原 request_id 不会自动重新生成。',
        'expired_at': row['artifacts_expired_at']}}


class OpenArtifactResponse(StreamingResponse):
    """Hold an open file so retention cannot break an already accepted download."""
    def __init__(self, handle, name):
        self.handle = handle
        super().__init__(self.chunks(), media_type='application/octet-stream', headers={
            'Content-Length': str(os.fstat(handle.fileno()).st_size),
            'Content-Disposition': f'attachment; filename="{name}"',
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    def chunks(self):
        while chunk := self.handle.read(64 * 1024):
            yield chunk

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.handle.close()


def create_app(state=None, artifacts=None, agents=None, base_url=None):
    queue = Queue(state or os.getenv('STATE_DIR', '/state'))
    artifact_root = Path(artifacts or os.getenv('ARTIFACT_DIR', '/artifacts'))
    agents = agents if agents is not None else json.loads(os.getenv('CAD_AGENTS', '{}'))
    if not isinstance(agents, dict) or any(not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', name) or not isinstance(token, str) or len(token) < 32 or token.startswith('__') for name, token in agents.items()):
        raise ValueError('CAD_AGENTS requires named agents with unique random tokens of at least 32 characters')
    if len(set(agents.values())) != len(agents):
        raise ValueError('Agent tokens must be different')
    management = Management(queue)
    origin = (base_url or os.getenv('PUBLIC_URL', 'http://192.168.1.100:8090')).rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('PUBLIC_URL must be an HTTP(S) origin')
    hosts = list(dict.fromkeys([parsed.hostname, 'localhost', '127.0.0.1'] + os.getenv('ALLOWED_HOSTS', '').split(',')))
    server = FastMCP('freecad-queue', stateless_http=True, json_response=True, streamable_http_path='/mcp',
        instructions='Submit FreeCAD Python code then poll the job. All lengths mm. Define result as the final FreeCAD document object with a valid solid Shape. Service never calls an LLM. One global FIFO worker; each agent sees only its own jobs. Use stable request_id for retries. Do not submit tokens in code. No GUI or manual export; service exports FCStd/STEP/STL.',
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[pattern for host in hosts if host for pattern in (host, host + ':*')]))

    def agent():
        value = agent_context.get()
        if value is None:
            raise PermissionError('Agent credentials required')
        return value

    @server.tool()
    async def submit_model(script: str, description: str = '', request_id: str = '') -> dict:
        """Queue FreeCAD Python code. Imports allowed: FreeCAD, Part, Mesh, MeshPart, Draft, math. Set result to a valid document object. No file I/O, subprocesses, networking, introspection or self-export. Reuse request_id only for identical retries."""
        validate(script)
        return queue.submit(agent(), script, description, request_id)

    @server.tool()
    async def get_job(job_id: str) -> dict:
        """Read your job and queue position. Poll every 2–3 seconds; download only after succeeded."""
        return queue.read(agent(), job_id)

    @server.tool()
    async def list_jobs(limit: int = 20) -> dict:
        """List your latest jobs, maximum 50. Other agents' jobs are not exposed."""
        return {'jobs': queue.list(agent(), limit)}

    @server.tool()
    async def cancel_job(job_id: str) -> dict:
        """Cancel your queued job or request termination of your running job. Poll for terminal status."""
        return queue.cancel(agent(), job_id)

    @server.tool()
    async def get_artifacts(job_id: str) -> dict:
        """Return your model URLs after success. Global retention is administrator-configurable. Expired outputs return ARTIFACTS_EXPIRED; submit a new request_id to regenerate. Download using your Bearer token."""
        row = queue.read(agent(), job_id)
        if row['artifacts_expired']:
            payload = expired_problem(row)
            return CallToolResult(isError=True, structuredContent=payload,
                content=[TextContent(type='text', text=json.dumps(payload, ensure_ascii=False))])
        names = row['result'].get('files', []) if row['status'] == 'succeeded' else []
        for name in names:
            if name in FILES and not (artifact_root / job_id / name).is_file():
                # Recheck tombstone in case retention ran after the first DB read.
                row = queue.read(agent(), job_id)
                if row['artifacts_expired']:
                    payload = expired_problem(row)
                else:
                    payload = {'job_id': job_id, 'files': {}, 'error': {
                        'code': 'ARTIFACT_NOT_FOUND', 'message': '任务产物文件缺失，请检查存储或重新建模。'}}
                return CallToolResult(isError=True, structuredContent=payload,
                    content=[TextContent(type='text', text=json.dumps(payload, ensure_ascii=False))])
        return {'job_id': job_id, 'status': row['status'], 'files': {
            name: origin + f'/files/{job_id}/{name}' for name in names if name in FILES}}

    async def health(request):
        try:
            value = queue.health()
        except sqlite3.Error:
            value = {'ready': False, 'worker': 'queue unavailable'}
        return JSONResponse(value, status_code=200 if value['ready'] else 503)

    async def info(request):
        return JSONResponse({'service': 'FreeCAD Agent Service', 'version': __version__, 'mcp': '/mcp', 'admin':'/admin',
                             'transport': 'Streamable HTTP', 'concurrency': 1})

    async def download(request):
        job_id, name = request.path_params['job_id'], request.path_params['name']
        row = None
        try:
            if str(uuid.UUID(job_id)) != job_id or name not in FILES:
                raise LookupError()
            row = queue.read(request.scope['agent'], job_id)
            if row['artifacts_expired']:
                return JSONResponse(expired_problem(row), status_code=410, headers={'Cache-Control': 'no-store'})
            if row['status'] != 'succeeded' or name not in row['result'].get('files', []):
                raise LookupError()
            folder = (artifact_root / job_id).resolve()
            path = (folder / name).resolve()
            if folder.parent != artifact_root.resolve() or path.parent != folder or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
                raise LookupError()
        except (LookupError, ValueError):
            if row is not None:
                current_row = queue.read(request.scope['agent'], job_id)
                if current_row['artifacts_expired']:
                    return JSONResponse(expired_problem(current_row), status_code=410, headers={'Cache-Control': 'no-store'})
            return JSONResponse({'error': {'code': 'ARTIFACT_NOT_FOUND', 'message': '文件不存在或无权访问。'}}, status_code=404)
        try:
            handle = path.open('rb')
        except FileNotFoundError:
            row = queue.read(request.scope['agent'], job_id)
            if row['artifacts_expired']:
                return JSONResponse(expired_problem(row), status_code=410, headers={'Cache-Control': 'no-store'})
            return JSONResponse({'error': {'code': 'ARTIFACT_NOT_FOUND', 'message': '任务产物文件缺失。'}}, status_code=404)
        try:
            row = queue.read(request.scope['agent'], job_id)
            if row['artifacts_expired']:
                handle.close()
                return JSONResponse(expired_problem(row), status_code=410, headers={'Cache-Control': 'no-store'})
            return OpenArtifactResponse(handle, name)
        except BaseException:
            handle.close()
            raise

    @asynccontextmanager
    async def lifespan(app):
        queue.initialize()
        management.initialize(agents)
        async with server.session_manager.run():
            yield

    admin_api = AdminAPI(management, artifact_root, origin, download)
    web = Path(__file__).with_name('web')
    async def admin_page(request):
        return FileResponse(web/'index.html', headers={'Cache-Control':'no-store',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            'X-Content-Type-Options':'nosniff'})
    inner = Starlette(routes=[Route('/', info), Route('/healthz', health), Route('/admin',admin_page),
        Mount('/admin/assets',StaticFiles(directory=web)),
        Route('/api/admin/{action:path}',admin_api.__call__,methods=['GET','POST','PUT','PATCH','DELETE','HEAD']),
        Route('/files/{job_id}/{name}', download), Mount('/', server.streamable_http_app())], lifespan=lifespan)

    class Gateway:
        def __init__(self):
            self.queue = queue
            self.mcp = server
            self.management = management
        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http':
                return await inner(scope, receive, send)
            headers = dict(scope.get('headers', []))
            if scope['path'] in ('/', '/healthz') and scope['method'] == 'GET':
                return await inner(scope, receive, send)
            if (scope['path'].rstrip('/')=='/admin' or scope['path'].startswith('/admin/assets/')) and scope['method'] in ('GET','HEAD'):
                return await inner(scope, receive, send)
            owner = None
            if not scope['path'].startswith('/api/admin/'):
                authorization = headers.get(b'authorization', b'')
                token = authorization[7:] if authorization.startswith(b'Bearer ') else b''
                try:
                    owner = management.authenticate(token.decode('utf-8'))
                except UnicodeDecodeError:
                    owner = None
                if owner is None:
                    return await JSONResponse({'error': 'Agent Bearer token required'}, 401, headers={'WWW-Authenticate': 'Bearer'})(scope, receive, send)
                if b'origin' in headers:
                    return await JSONResponse({'error': 'Browser origins are not accepted'}, 403)(scope, receive, send)
            # Stateless mode has no listen stream: return 405 instead of a hanging SSE GET.
            if scope['path'].rstrip('/') == '/mcp' and scope['method'] != 'POST':
                return await JSONResponse({'error': 'Use MCP POST requests'}, 405, headers={'Allow': 'POST'})(scope, receive, send)
            messages, size = [], 0
            while True:
                event = await receive()
                if event['type'] == 'http.disconnect':
                    return
                size += len(event.get('body', b''))
                if size > 512 * 1024:
                    return await JSONResponse({'error': 'Request exceeds 512KB'}, 413)(scope, receive, send)
                messages.append(event)
                if not event.get('more_body', False):
                    break
            async def replay():
                if messages:
                    return messages.pop(0)
                return await receive()
            marker = agent_context.set(owner)
            scope['agent'] = owner
            try:
                await inner(scope, replay, send)
            finally:
                agent_context.reset(marker)
    return Gateway()


def application():
    return create_app()
