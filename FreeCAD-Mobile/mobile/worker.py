"""One durable mobile orchestrator, separate from the existing global CAD FIFO."""
import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import time
from urllib.parse import urlsplit
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mobile.store import Store
from mobile.provider import generate
from mobile.script_contract import prepare_script

DEMO='''import FreeCAD as App
import Part
length, width, thickness, hole_diameter = 60, 30, 5, 6
doc = App.newDocument("Plate")
result = doc.addObject("Part::Feature", "Plate")
result.Shape = Part.makeBox(length,width,thickness).cut(Part.makeCylinder(hole_diameter/2,thickness+2,App.Vector(length/2,width/2,-1)))
doc.recompute()
'''

class Cancelled(Exception): pass

async def cad_health():
    try:
        async with httpx.AsyncClient(timeout=5,trust_env=False) as client:
            response=await client.get(os.environ['CAD_URL'].removesuffix('/mcp')+'/healthz')
        data=response.json()
        return {'ready':response.is_success and data.get('ready') is True,
                'message':('执行端在线' if response.is_success and data.get('ready') is True else
                    'FreeCAD 执行端未就绪或心跳已过期。任务保留在队列，请检查或重启 CAD worker；恢复后会继续。'),
                'checked_at':time.time()}
    except Exception:
        return {'ready':False,'message':'无法连接 FreeCAD 服务，正在重试。请检查 NAS 与 CAD 容器；不要重复提交任务。','checked_at':time.time()}

async def bounded_call(name,args):
    async with asyncio.timeout(35):
        return await cad_call(name,args)

async def cad_call(name,args):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+os.environ['CAD_TOKEN']},timeout=30,trust_env=False) as http:
        async with streamable_http_client(os.environ['CAD_URL'],http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                result=await client.call_tool(name,args)
                data=result.structuredContent or json.loads(''.join(c.text for c in result.content if c.type=='text'))
                if result.isError:
                    raise ValueError(json.dumps(data,ensure_ascii=False))
                return data

def cancelled(store,job_id):
    if store.job(job_id)['cancel']: raise Cancelled()

async def interruptible(store,job_id,awaitable):
    task=asyncio.create_task(awaitable)
    try:
        while not task.done():
            cancelled(store,job_id)
            await asyncio.wait({task},timeout=.5)
        cancelled(store,job_id)
        return await task
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task,return_exceptions=True)

async def execute(store,job_id,script,attempt):
    cancelled(store,job_id)
    store.update(job_id,script=script,stage='submitting',position=None)
    # Idempotent after worker restarts or a lost submit response.
    remote=await bounded_call('submit_model',{'script':script,'description':store.job(job_id)['prompt'][:1900],'request_id':job_id+'-'+str(attempt)})
    remote_id=remote['job_id']; store.update(job_id,remote=remote_id)
    try:
        while True:
            cancelled(store,job_id)
            health=await cad_health()
            store.update(job_id,cad_health=json.dumps(health,ensure_ascii=False))
            try:
                row=await bounded_call('get_job',{'job_id':remote_id})
            except ValueError:
                raise
            except Exception:
                # Keep the existing remote task; reconnect without another LLM call or submit.
                health={'ready':False,'message':'任务状态查询暂时失败，正在重新连接。原任务已保留，请勿重复提交。','checked_at':time.time()}
                store.update(job_id,stage='cad_unavailable',cad_health=json.dumps(health,ensure_ascii=False))
                await asyncio.sleep(2)
                continue
            status=row['status']
            stage='cad_queued' if status=='queued' else 'building'
            if not health['ready'] and status in ('queued','running'):
                stage='cad_unavailable'
            store.update(job_id,stage=stage,position=row.get('queue_position'))
            if status=='succeeded': return row['result']
            if status in ('failed','cancelled','interrupted'):
                raise ValueError('FreeCAD 执行未完成：'+str(row.get('error') or status))
            await asyncio.sleep(2)
    except (Cancelled,asyncio.CancelledError):
        try:
            await bounded_call('cancel_job',{'job_id':remote_id})
        except Exception:
            pass  # process() persists cancelling and retries; never claim cancellation succeeded.
        raise

async def archive(store,job_id):
    row=store.job(job_id); files=await cad_call('get_artifacts',{'job_id':row['remote']})
    temporary=store.root/'files'/(job_id+'.partial'); target=store.root/'files'/job_id
    temporary.mkdir(exist_ok=True)
    try:
        async with httpx.AsyncClient(headers={'Authorization':'Bearer '+os.environ['CAD_TOKEN']},timeout=60,trust_env=False,follow_redirects=False) as client:
            for name in ('model.FCStd','model.step','model.stl','report.json'):
                cancelled(store,job_id)
                url=files['files'][name]
                if urlsplit(url).netloc!=urlsplit(os.environ['CAD_URL']).netloc or urlsplit(url).scheme!=urlsplit(os.environ['CAD_URL']).scheme:
                    raise ValueError('CAD 返回的下载地址与配置不一致，请核对 CAD 的 PUBLIC_URL')
                size=0
                async with client.stream('GET',url) as response:
                    if response.status_code==410: raise ValueError('CAD 产物已被清理，请重新生成；脚本仍已保存')
                    response.raise_for_status()
                    with (temporary/name).open('wb') as out:
                        async for chunk in response.aiter_bytes():
                            cancelled(store,job_id); size+=len(chunk)
                            if size>40*1024*1024: raise ValueError('产物超过归档大小限制')
                            out.write(chunk)
        (temporary/'source.py').write_text(row['script'],encoding='utf-8')
        cancelled(store,job_id)
        if target.exists(): remove_directory(store.root/'files',target.name)
        temporary.rename(target)
        store.update(job_id,archived=1,status='review',stage='review',error=None)
    finally:
        remove_directory(store.root/'files',temporary.name)

def remove_directory(root,name):
    root=root.resolve(); path=root/name
    if Path(name).name!=name or path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()) or path.resolve().parent!=root:
        raise ValueError('拒绝清理不安全的目录')
    if path.exists(): shutil.rmtree(path)

async def process(store,job_id):
    row=store.job(job_id)
    try:
        cancelled(store,job_id)
        target=store.root/'files'/job_id
        # Recover a completed atomic rename if the process stopped before the DB update.
        if all((target/name).is_file() for name in ('model.FCStd','model.step','model.stl','report.json','source.py')):
            store.update(job_id,archived=1,status='review',stage='review',error=None)
            return
        if row['remote'] or (row['script'] and row['stage'] in ('submitting','cad_queued','building','archiving')):
            # Reconcile known remote task instead of repeating paid model generation.
            script=row['script']; attempt=row['attempt']
            report=await execute(store,job_id,script,attempt)
            store.update(job_id,report=json.dumps(report),stage='archiving')
            await archive(store,job_id); return
        config=json.loads(store.cipher.decrypt(_config(store,job_id).encode()))
        base=store.job(row['parent'])['script'] if row['parent'] else None
        inherited_images=[]
        parent=row
        while parent.get('parent') and not inherited_images:
            parent=store.job(parent['parent']); inherited_images=parent['images']
        images=['data:image/jpeg;base64,'+base64.b64encode((store.root/'uploads'/name).read_bytes()).decode() for name in (row['images'] or inherited_images)]
        if images and not config.get('vision'):
            raise ValueError('本次修改沿用了参考图，请使用已启用图片输入的模型配置')
        usage=row['usage']; feedback=None
        for attempt in range(2):
            cancelled(store,job_id)
            if row['stage']=='demo':
                plan={'summary':'60 × 30 × 5 mm 矩形板，中心 Ø6 mm 贯穿孔','dimensions':['长 60 mm','宽 30 mm','厚 5 mm','中心孔 Ø6 mm'],'assumptions':[],'questions':[],'script':DEMO}
            else:
                store.update(job_id,stage='generating' if attempt==0 else 'repairing',attempt=attempt)
                try:
                    plan,used=await interruptible(store,job_id,generate(config,row['prompt'],images,base,feedback))
                except (ValueError,KeyError,TypeError) as exc:
                    if attempt: raise
                    feedback='上次响应格式不符合约定，请仅返回约定的 JSON 对象。'+store.safe_error(exc)
                    continue
                usage.append(used)
            store.update(job_id,plan=json.dumps({k:v for k,v in plan.items() if k!='script'},ensure_ascii=False),script=plan['script'],usage=json.dumps(usage))
            if plan.get('questions'):
                store.update(job_id,status='needs_input',stage='needs_input'); return
            try:
                script=prepare_script(plan['script'])
                report=await execute(store,job_id,script,attempt)
                store.update(job_id,report=json.dumps(report),stage='archiving')
                await archive(store,job_id); return
            except ValueError as exc:
                if attempt or row['stage']=='demo' or '产物' in str(exc): raise
                feedback=store.safe_error(exc)
                if 'Define result as the final FreeCAD document object' in feedback:
                    feedback+='\n必须把最后几何体放入文档对象：result = doc.addObject("Part::Feature", "Result"); result.Shape = final_shape; doc.recompute()。若函数返回文档对象，应在模块顶层写 result = build_model()。不要改变零件尺寸。'
                base=plan['script']
                store.update(job_id,remote=None)
    except Cancelled:
        current=store.job(job_id)
        if current.get('remote'):
            try:
                await bounded_call('cancel_job',{'job_id':current['remote']})
            except Exception as exc:
                store.update(job_id,status='cancelling',error='取消请求暂未送达 CAD，恢复连接后会重试：'+store.safe_error(exc))
                return
        remove_directory(store.root/'files',job_id)
        store.update(job_id,status='cancelled',stage='cancelled',archived=0)
    except Exception as exc:
        remove_directory(store.root/'files',job_id)
        store.update(job_id,status='failed',stage='failed',error=store.safe_error(exc),archived=0)

def _config(store,job_id):
    with store.db() as db: return db.execute('SELECT config FROM jobs WHERE id=?',(job_id,)).fetchone()[0]

async def main():
    store=Store()
    # OS lock prevents two orchestrators claiming jobs or reconciling simultaneously.
    lock=(store.root/'worker.lock').open('a+')
    if os.name=='posix':
        import fcntl
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    with store.db() as db:
        db.execute("UPDATE jobs SET status='interrupted',stage='failed',error='移动端后台在模型调用过程中重启；为避免重复计费，未自动重试，请手动重新生成' WHERE status IN ('working','cancelling') AND stage IN ('generating','repairing')")
        db.execute("UPDATE jobs SET status='pending' WHERE status IN ('working','cancelling')")
    async def heartbeat():
        while True:
            with store.db() as db: db.execute('INSERT OR REPLACE INTO runtime VALUES(1,?)',(time.time(),))
            await asyncio.sleep(2)
    beat=asyncio.create_task(heartbeat())
    try:
        while True:
            with store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute("SELECT id FROM jobs WHERE status IN ('pending','cancelling') ORDER BY created LIMIT 1").fetchone()
                if row: db.execute("UPDATE jobs SET status='working' WHERE id=?",(row['id'],))
            if row:
                await process(store,row['id'])
                if store.job(row['id'])['status']=='cancelling': await asyncio.sleep(2)
            else:
                await housekeeping(store)
                await asyncio.sleep(1)
    finally: beat.cancel(); lock.close()

async def housekeeping(store):
    with store.db() as db:
        deleted=[r[0] for r in db.execute('SELECT j.id FROM jobs j JOIN projects p ON j.project=p.id WHERE p.deleted=1')]
        used={name for row in db.execute('SELECT images FROM jobs j JOIN projects p ON j.project=p.id WHERE p.deleted=0') for name in json.loads(row[0])}
    for name in deleted: remove_directory(store.root/'files',name)
    for path in (store.root/'files').glob('*.partial'):
        remove_directory(store.root/'files',path.name)
    for path in (store.root/'uploads').glob('*.jpg'):
        if path.name not in used and time.time()-path.stat().st_mtime>86400: path.unlink()

if __name__=='__main__': asyncio.run(main())
