import asyncio
import hmac
import json
import os
import sqlite3
from pathlib import Path
import time
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from cad_service.management import AdminError


def disk_usage(root):
    root = Path(root)
    if root.is_symlink() or getattr(root,'is_junction',lambda:False)():
        return {'bytes':0,'files':0,'unreadable':1}
    total, files, unreadable = 0, 0, 0
    def scan(folder):
        nonlocal total, files, unreadable
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            scan(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except FileNotFoundError:
                        pass
                    except OSError:
                        unreadable += 1
        except FileNotFoundError:
            pass
        except OSError:
            unreadable += 1
    scan(root)
    return {'bytes':total, 'files':files, 'unreadable':unreadable}


class AdminAPI:
    def __init__(self, management, artifacts, origin, download):
        self.management = management
        self.queue = management.queue
        self.artifacts = artifacts
        self.origin = origin
        self.download = download
        self.storage_cache = (0, {})

    def cookie(self, response, session):
        response.set_cookie('cad_admin', session, max_age=8*3600, httponly=True,
            secure=urlsplit(self.origin).scheme=='https', samesite='strict', path='/api/admin')

    async def __call__(self, request):
        try:
            return await self.dispatch(request)
        except AdminError as exc:
            return JSONResponse({'error':str(exc)}, status_code=exc.status, headers={'Cache-Control':'no-store'})
        except (ValueError, TypeError, KeyError) as exc:
            return JSONResponse({'error':str(exc)}, status_code=400, headers={'Cache-Control':'no-store'})
        except sqlite3.Error:
            return JSONResponse({'error':'管理数据库暂时不可用，请稍后重试'},status_code=503)

    async def dispatch(self, request):
        action = request.path_params['action'].strip('/')
        method = request.method
        supplied_origin = request.headers.get('origin')
        if supplied_origin is not None and supplied_origin != self.origin:
            raise AdminError('不允许来自其他站点的管理请求', 403)
        if method not in ('GET','HEAD') and 'application/json' not in request.headers.get('content-type',''):
            raise AdminError('请求须使用 JSON', 415)
        body = await request.json() if method not in ('GET','HEAD') else {}
        if not isinstance(body, dict):
            raise AdminError('请求须为 JSON 对象')
        if action == 'login' and method == 'POST':
            ip = request.client.host if request.client else 'unknown'
            token, csrf = await asyncio.to_thread(self.management.login, body.get('username'), body.get('password'), ip)
            response = JSONResponse({'csrf':csrf}, headers={'Cache-Control':'no-store'})
            self.cookie(response, token)
            return response
        token = request.cookies.get('cad_admin', '')
        session = self.management.session(token)
        if not session:
            raise AdminError('请先登录管理页', 401)
        if method not in ('GET','HEAD') and not hmac.compare_digest(request.headers.get('x-csrf-token',''), session['csrf']):
            raise AdminError('请求校验失败，请刷新后重试', 403)

        result = None
        if action == 'session' and method == 'GET':
            result = session
        elif action == 'logout' and method == 'POST':
            self.management.logout(token)
            response = JSONResponse({'ok':True})
            response.delete_cookie('cad_admin', path='/api/admin')
            return response
        elif action == 'password' and method == 'POST':
            token, csrf = await asyncio.to_thread(self.management.change_password,
                body.get('current_password'), body.get('new_password'), body.get('username'))
            response = JSONResponse({'ok':True,'csrf':csrf})
            self.cookie(response, token)
            return response
        elif action == 'overview' and method == 'GET':
            result = self.management.overview()
            result['storage'] = await self.storage()
            for row in result['stats']:
                row['storage_bytes'] = result['storage'].get('by_agent',{}).get(row['agent'],0)
        elif action == 'agents' and method == 'GET':
            result = {'agents':self.management.list_agents()}
        elif action == 'agents' and method == 'POST':
            name = body.get('agent_id')
            token = self.management.create_agent(name, body.get('display_name',''), body.get('pending_limit'))
            result = self.credential(name, token)
        elif action.startswith('agents/'):
            parts = action.split('/')
            if len(parts)==3 and parts[2]=='rotate' and method=='POST':
                result = self.credential(parts[1], self.management.rotate_agent(parts[1]))
            elif len(parts)==2 and method=='PATCH':
                self.management.update_agent(parts[1], body)
                result = {'ok':True}
        elif action == 'settings' and method == 'GET':
            result = self.queue.settings()
        elif action == 'settings' and method == 'PUT':
            result = self.management.save_settings(body.get('settings',{}), body.get('confirmation'))
        elif action == 'retention/preview' and method == 'POST':
            result = self.management.preview_retention(body.get('retention_count'))
            sizes = await asyncio.to_thread(lambda: {row['id']:disk_usage(self.artifacts/row['id'])['bytes'] for row in result['jobs']})
            result['estimated_bytes'] = sum(sizes.values())
        elif action == 'jobs' and method == 'GET':
            result = self.management.jobs(request.query_params.get('agent',''), request.query_params.get('status',''),
                int(request.query_params.get('limit','30')), int(request.query_params.get('offset','0')))
        elif action.startswith('jobs/'):
            parts = action.split('/')
            job_id = parts[1]
            if len(parts)==2 and method=='GET':
                result = self.queue.read(self.management.owner(job_id), job_id)
                with self.queue.connection() as conn:
                    result['script'] = conn.execute('SELECT script FROM jobs WHERE id=?',(job_id,)).fetchone()[0]
                if result['artifacts_available']:
                    result['downloads'] = {name:f'/api/admin/files/{job_id}/{name}' for name in (result.get('result') or {}).get('files',[])
                        if name in {'source.py','model.FCStd','model.step','model.stl','report.json'}}
            elif len(parts)==3 and parts[2]=='cancel' and method=='POST':
                result = self.queue.cancel(self.management.owner(job_id),job_id)
            elif len(parts)==4 and parts[2:] == ['artifacts','preview'] and method=='GET':
                result = self.management.preview_delete(job_id)
                result['estimated_bytes'] = (await asyncio.to_thread(disk_usage, self.artifacts/job_id))['bytes']
            elif len(parts)==4 and parts[2:] == ['artifacts','delete'] and method=='POST':
                self.management.delete_artifacts(job_id,body.get('confirmation'))
                self.storage_cache = (0,{})
                result = {'ok':True,'message':'已标记删除，Worker 空闲后完成清理。'}
        elif action.startswith('files/') and method in ('GET','HEAD'):
            parts = action.split('/')
            if len(parts)==3:
                request.scope['agent'] = self.management.owner(parts[1])
                request.path_params.update(job_id=parts[1],name=parts[2])
                return await self.download(request)
        if result is None:
            raise AdminError('管理接口不存在或方法不支持',404)
        return JSONResponse(result, headers={'Cache-Control':'no-store'})

    def credential(self, name, token):
        return {'agent_id':name, 'token':token,'url':self.origin+'/mcp',
            'headers':{'Authorization':'Bearer '+token}, 'message':'口令仅显示这一次，请立即保存。'}

    async def storage(self):
        if time.monotonic()-self.storage_cache[0]>5:
            value = await asyncio.to_thread(disk_usage,self.artifacts)
            with self.queue.connection() as conn:
                jobs = [(row[0],row[1]) for row in conn.execute('SELECT id,agent FROM jobs')]
            def per_agent():
                usage = {}
                for job,agent in jobs:
                    usage[agent] = usage.get(agent,0)+disk_usage(self.artifacts/job)['bytes']
                return usage
            value['by_agent'] = await asyncio.to_thread(per_agent)
            self.storage_cache = (time.monotonic(),value)
        return self.storage_cache[1]
