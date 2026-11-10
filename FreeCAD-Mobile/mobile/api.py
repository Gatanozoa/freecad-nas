import base64
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit
import uuid
from PIL import Image, ImageOps
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from mobile.store import Store, password_ok, password_hash
from mobile.provider import probe, normalize_base_url
from mobile import __version__

FILES = {'model.FCStd','model.step','model.stl','report.json','source.py'}

def create_app(store=None):
    store = store or Store()
    async def endpoint(request):
        action = request.path_params.get('action','')
        try:
            if request.method != 'GET':
                origin = request.headers.get('origin')
                if origin and origin != os.environ.get('PUBLIC_URL', str(request.base_url).rstrip('/')):
                    return JSONResponse({'error':'页面来源不匹配，请使用部署配置中的访问地址'},403)
            if action == 'login' and request.method == 'POST':
                data = await request.json()
                ip = request.client.host
                with store.db() as db:
                    attempt = db.execute('SELECT * FROM attempts WHERE ip=?',(ip,)).fetchone()
                    if attempt and attempt['count']>=5 and time.time()-attempt['since']<300:
                        return JSONResponse({'error':'登录失败次数过多，请五分钟后重试'},429)
                    account = db.execute('SELECT * FROM account').fetchone()
                    if data.get('username')!=account['username'] or not password_ok(str(data.get('password','')),account['password']):
                        since = attempt['since'] if attempt and time.time()-attempt['since']<300 else time.time()
                        count = attempt['count']+1 if attempt and since==attempt['since'] else 1
                        db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',(ip,count,since))
                        response = JSONResponse({'error':'用户名或密码不正确'},401)
                    else:
                        db.execute('DELETE FROM attempts WHERE ip=?',(ip,))
                        token=secrets.token_urlsafe(32)
                        db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
                        db.execute('INSERT INTO sessions VALUES(?,?)',(hashlib.sha256(token.encode()).hexdigest(),time.time()+86400*7))
                        response=JSONResponse({'ok':True})
                        response.set_cookie('mobile_session',token,max_age=86400*7,httponly=True,samesite='strict',secure=os.environ.get('PUBLIC_URL','').startswith('https://'),path='/api')
                return response
            token = request.cookies.get('mobile_session','')
            digest=hashlib.sha256(token.encode()).hexdigest()
            with store.db() as db:
                authenticated=db.execute('SELECT 1 FROM sessions WHERE token=? AND expires>?',(digest,time.time())).fetchone()
            if not authenticated:
                return JSONResponse({'error':'请登录后继续'},401)
            if action=='session':
                with store.db() as db:
                    username=db.execute('SELECT username FROM account').fetchone()[0]
                    heartbeat=db.execute('SELECT heartbeat FROM runtime WHERE id=1').fetchone()
                return JSONResponse({'username':username,'version':__version__,'worker_ready':bool(heartbeat and time.time()-heartbeat[0]<20)})
            if action=='logout' and request.method=='POST':
                with store.db() as db:
                    db.execute('DELETE FROM sessions WHERE token=?',(digest,))
                response=JSONResponse({'ok':True}); response.delete_cookie('mobile_session',path='/api'); return response
            if action=='password' and request.method=='POST':
                data=await request.json()
                if not 8<=len(data.get('new',''))<=128:
                    raise ValueError('新密码需要 8～128 个字符')
                with store.db() as db:
                    account=db.execute('SELECT * FROM account').fetchone()
                    if not password_ok(data.get('old',''),account['password']):
                        raise ValueError('当前密码不正确')
                    db.execute('UPDATE account SET password=?',(password_hash(data['new']),))
                    db.execute('DELETE FROM sessions')
                return JSONResponse({'ok':True})
            if action=='providers':
                if request.method=='GET':
                    with store.db() as db:
                        ids=[r[0] for r in db.execute('SELECT id FROM providers ORDER BY rowid')]
                    default=store.default_provider()
                    return JSONResponse([{**store.provider(p),'is_default':p==default} for p in ids])
                if request.method=='POST':
                    data=await request.json(); provider_id=data.get('id') or str(uuid.uuid4())
                    data['url']=normalize_base_url(data.get('url',''))
                    for field in ('name','model'):
                        if not isinstance(data.get(field),str) or not 1<=len(data[field].strip())<=200:
                            raise ValueError('请填写配置名称和模型名称（最多200字）')
                        data[field]=data[field].strip()
                    if not data.get('name') or not data.get('model'):
                        raise ValueError('请填写配置名称和模型名称')
                    key=data.get('key','')
                    if not isinstance(key,str): raise ValueError('Key 格式不正确')
                    key=key.strip()
                    if key.lower().startswith('bearer '): key=key[7:].strip()
                    old=None
                    if not key:
                        if not data.get('id'): raise ValueError('首次添加模型时需要填写 API Key')
                        old=store.provider(provider_id,True); key=old['key']
                    if len(key)>4096:
                        raise ValueError('Key 过长')
                    record={k:data[k] for k in ('name','url','model')}
                    record['vision']=bool(data.get('vision')); record['verified_text']=None; record['verified_vision']=None
                    with store.db() as db:
                        db.execute('INSERT OR REPLACE INTO providers VALUES(?,?,?)',(provider_id,json.dumps(record),store.cipher.encrypt(key.encode()).decode()))
                        if data.get('make_default') or not db.execute("SELECT 1 FROM preferences WHERE name='default_provider'").fetchone():
                            db.execute("INSERT OR REPLACE INTO preferences VALUES('default_provider',?)",(provider_id,))
                    return JSONResponse(store.provider(provider_id))
            if action=='preferences':
                if request.method=='GET': return JSONResponse({'default_provider':store.default_provider()})
                if request.method=='POST':
                    data=await request.json(); provider_id=data.get('default_provider')
                    store.provider(provider_id)
                    with store.db() as db:
                        db.execute("INSERT OR REPLACE INTO preferences VALUES('default_provider',?)",(provider_id,))
                    return JSONResponse({'default_provider':provider_id})
            if action.startswith('providers/'):
                provider_id=action.split('/')[1]
                if request.method=='DELETE':
                    with store.db() as db:
                        db.execute('DELETE FROM providers WHERE id=?',(provider_id,))
                    return JSONResponse({'ok':True})
                if request.method=='POST' and action.endswith('/test'):
                    data=await request.json(); config=store.provider(provider_id,True)
                    result=await probe(config,bool(data.get('vision')))
                    config.pop('key'); config.pop('id'); config['verified_vision' if data.get('vision') else 'verified_text']=time.time()
                    with store.db() as db:
                        db.execute('UPDATE providers SET data=? WHERE id=?',(json.dumps(config),provider_id))
                    return JSONResponse(result)
            if action=='cad' and request.method=='GET':
                import httpx
                async with httpx.AsyncClient(timeout=8,trust_env=False) as client:
                    response=await client.get(os.environ['CAD_URL'].removesuffix('/mcp')+'/healthz')
                return JSONResponse({'ready':response.is_success and response.json().get('ready',False)})
            if action=='uploads' and request.method=='POST':
                if request.headers.get('content-type','').split(';')[0] not in ('image/jpeg','image/png','image/webp'):
                    raise ValueError('支持 JPEG、PNG 和 WebP 图片')
                raw=await request.body()
                if len(raw)>10*1024*1024:
                    raise ValueError('每张图片最大 10MB，请裁剪后上传')
                try:
                    with Image.open(io.BytesIO(raw)) as source:
                        if source.width*source.height>32_000_000:
                            raise ValueError('图片超过 3200 万像素，请裁剪重点区域')
                        image=ImageOps.exif_transpose(source).convert('RGB')
                        image.load()
                except (OSError,Image.DecompressionBombError) as exc:
                    raise ValueError('无法读取图片') from exc
                name=str(uuid.uuid4())+'.jpg'
                image.save(store.root/'uploads'/name,'JPEG',quality=95)
                return JSONResponse({'id':name})
            if action.startswith('uploads/') and request.method=='GET':
                name=action.split('/')[1]
                if str(uuid.UUID(name.removesuffix('.jpg')))+'.jpg'!=name:
                    raise ValueError('图片编号无效')
                return FileResponse(store.root/'uploads'/name,headers={'Cache-Control':'no-store'})
            if action=='jobs':
                if request.method=='GET':
                    with store.db() as db:
                        project=request.query_params.get('project')
                        if project:
                            ids=[r[0] for r in db.execute('SELECT j.id FROM jobs j JOIN projects p ON j.project=p.id WHERE p.deleted=0 AND j.project=? ORDER BY j.version', (project,))]
                        else:
                            ids=[r[0] for r in db.execute('SELECT j.id FROM jobs j JOIN projects p ON j.project=p.id WHERE p.deleted=0 ORDER BY j.created DESC LIMIT 300')]
                    rows=[store.job(i) for i in ids]
                    for row in rows:
                        row.pop('script',None)
                    return JSONResponse(rows)
                if request.method=='POST':
                    data=await request.json(); prompt=data.get('prompt','').strip(); images=data.get('images',[])
                    if not 1<=len(prompt)<=12000 or not isinstance(images,list) or len(images)>4:
                        raise ValueError('请填写 1～12000 字的需求，参考图最多四张')
                    for name in images:
                        if not isinstance(name,str) or str(uuid.UUID(name.removesuffix('.jpg')))+'.jpg'!=name or not (store.root/'uploads'/name).is_file():
                            raise ValueError('参考图不存在，请重新上传')
                    if images and not data.get('demo') and not store.provider(data.get('provider')).get('vision'):
                        raise ValueError('该配置未启用图片输入，请到设置中选择支持图片的模型')
                    job_id=store.submit(prompt,data.get('provider'),images,data.get('parent'),bool(data.get('demo')),data.get('request_id'))
                    return JSONResponse(store.job(job_id),202)
            if action.startswith('jobs/'):
                parts=action.split('/'); job=store.job(parts[1])
                if len(parts)==2 and request.method=='GET':
                    return JSONResponse(job)
                if len(parts)==3 and request.method=='POST':
                    if parts[2]=='confirm':
                        if job['status']!='review' or not job['archived']:
                            raise ValueError('请等待草稿生成并归档完成')
                        store.update(job['id'],status='confirmed',stage='confirmed')
                    elif parts[2]=='cancel':
                        with store.db() as db:
                            db.execute("UPDATE jobs SET cancel=1,status=CASE WHEN status='pending' THEN 'cancelled' ELSE 'cancelling' END,updated=? WHERE id=? AND status IN ('pending','working','cancelling')",(time.time(),job['id']))
                    else:
                        raise LookupError('操作不存在')
                    return JSONResponse(store.job(job['id']))
                if len(parts)==4 and parts[2]=='files' and request.method=='GET':
                    name=parts[3]
                    if name not in FILES:
                        raise LookupError('文件不存在')
                    if not job['archived']:
                        return JSONResponse({'error':'本版本模型尚未归档，请重新生成','code':'NOT_ARCHIVED'},409)
                    if job['status']!='confirmed' and name not in ('model.stl','report.json'):
                        return JSONResponse({'error':'请先确认草稿'},409)
                    file=store.root/'files'/job['id']/name
                    if not file.is_file():
                        return JSONResponse({'error':'归档文件缺失，请重新生成','code':'ARCHIVE_MISSING'},410)
                    return FileResponse(file,filename=name,headers={'Cache-Control':'no-store'})
            if action.startswith('projects/'):
                project=action.split('/')[1]
                if request.method=='PATCH':
                    data=await request.json(); name=data.get('name','').strip()
                    if not 1<=len(name)<=80:
                        raise ValueError('名称需要 1～80 个字符')
                    with store.db() as db:
                        db.execute('UPDATE projects SET name=? WHERE id=?',(name,project))
                    return JSONResponse({'ok':True})
                if request.method=='DELETE':
                    with store.db() as db:
                        if db.execute("SELECT 1 FROM jobs WHERE project=? AND status IN ('pending','working','cancelling')",(project,)).fetchone():
                            raise ValueError('请先取消正在处理的任务')
                        db.execute('UPDATE projects SET deleted=1 WHERE id=?',(project,))
                    return JSONResponse({'ok':True})
            if action=='storage':
                total=sum(p.stat().st_size for p in (store.root/'files').glob('*/*') if p.is_file())
                return JSONResponse({'bytes':total})
            raise LookupError('接口不存在')
        except LookupError as exc:
            return JSONResponse({'error':str(exc)},404)
        except (ValueError,KeyError,TypeError) as exc:
            return JSONResponse({'error':store.safe_error(exc)},400)
        except Exception as exc:
            return JSONResponse({'error':'操作未完成，请检查连接后重试','detail':store.safe_error(exc)},502)

    class BodyLimit:
        def __init__(self,app): self.app=app
        async def __call__(self,scope,receive,send):
            if scope['type']!='http': return await self.app(scope,receive,send)
            count=0
            async def bounded():
                nonlocal count
                message=await receive(); count+=len(message.get('body',b''))
                if count>11*1024*1024: raise ValueError('请求过大，最多 10MB 图片')
                return message
            await self.app(scope,bounded,send)
    web=Path(__file__).parent/'web'
    async def index(request): return FileResponse(web/'index.html',headers={'Cache-Control':'no-cache'})
    async def service_worker(request): return FileResponse(web/'sw.js',media_type='text/javascript',headers={'Cache-Control':'no-cache'})
    return BodyLimit(Starlette(routes=[Route('/api/{action:path}',endpoint,methods=['GET','POST','PATCH','DELETE']),Route('/',index),Route('/sw.js',service_worker),Mount('/static',app=StaticFiles(directory=web))]))
