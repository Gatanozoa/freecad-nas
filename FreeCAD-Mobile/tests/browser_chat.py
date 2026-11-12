"""Isolated chat UX checks with real HTTP, local saved STL, and no NAS/model requests."""
import bootstrap
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from cryptography.fernet import Fernet
from PIL import Image
import uvicorn
from playwright.sync_api import sync_playwright, expect
from starlette.testclient import TestClient
from mobile.store import Store
from mobile.api import create_app

qa=bootstrap.ROOT/'.qa'; screens=qa/'screens';screens.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=qa) as directory:
    os.environ['MASTER_KEY']=Fernet.generate_key().decode()
    sock=socket.socket();sock.bind(('127.0.0.1',0));origin='http://127.0.0.1:'+str(sock.getsockname()[1]);os.environ['PUBLIC_URL']=origin
    store=Store(directory);app=create_app(store)
    client=TestClient(app);client.post('/api/login',json={'username':'admin','password':'admin'})
    provider=client.post('/api/providers',json={'name':'零件建模','url':'https://example.invalid/v1','model':'test-model','key':'local-test-only','vision':True}).json()['id']
    # Deterministic closed box mesh for browser rendering/download checks.
    import struct
    fixture=Path(directory)/'fixture.stl'
    vertices=[(0,0,0),(60,0,0),(60,30,0),(0,30,0),(0,0,5),(60,0,5),(60,30,5),(0,30,5)]
    faces=[(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(3,7,6),(3,6,2),(0,4,7),(0,7,3),(1,2,6),(1,6,5)]
    payload=b'Browser fixture'.ljust(80,b'\0')+struct.pack('<I',len(faces))
    for a,b,c in faces:payload+=struct.pack('<12fH',0,0,0,*vertices[a],*vertices[b],*vertices[c],0)
    fixture.write_bytes(payload)
    def finish(job):
        target=store.root/'files'/job;target.mkdir(parents=True,exist_ok=True);shutil.copy(fixture,target/'model.stl')
        store.update(job,status='review',stage='review',archived=1,report=json.dumps({'dimensions':[60,30,5]}),plan=json.dumps({'summary':'带中心贯穿孔的矩形板','assumptions':['单位为毫米']}))
    server=uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    while not server.started:time.sleep(.05)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel=os.environ.get('BROWSER_CHANNEL') or None,headless=True,args=['--enable-unsafe-swiftshader'])
            context=browser.new_context(viewport={'width':390,'height':844},accept_downloads=True)
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin);page.locator('[name=password]').fill('admin');page.locator('#login-form button').click()
            expect(page.locator('#main h1')).to_have_text('创建模型');expect(page.locator('#provider')).to_have_value(provider)
            expect(page.locator('#create-add-provider')).to_have_count(0)
            assert page.locator('html').evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(16, 17, 19)'
            boxes=[page.locator(s).bounding_box() for s in ('#attach','#provider','#generate')]
            assert all(abs(b['height']-44)<1 for b in boxes),boxes
            assert max(b['y'] for b in boxes)-min(b['y'] for b in boxes)<1,boxes
            page.screenshot(path=str(screens/'11-chat-home-mobile.png'),full_page=True)
            page.locator('#prompt').fill('创建 60×30×5 mm 矩形板，中心开直径 6 mm 的孔。')
            image=io.BytesIO();Image.new('RGB',(30,30),'white').save(image,'PNG')
            page.locator('#image-input').set_input_files({'name':'reference.png','mimeType':'image/png','buffer':image.getvalue()})
            expect(page.locator('#images img')).to_have_count(1)
            with page.expect_response(lambda r:r.url==origin+'/api/jobs' and r.request.method=='POST') as response:page.locator('#generate').click()
            job=response.value.json()['id'];expect(page.locator('.user-message')).to_contain_text('矩形板')
            expect(page.locator('.user-message img')).to_have_count(1);expect(page.locator('#generate')).to_be_disabled()
            store.update(job,status='working',stage='cad_queued',position=2)
            expect(page.locator('.thinking')).to_contain_text('等待 FreeCAD 建模',timeout=15000)
            page.locator('#prompt').fill('将厚度改为 8 mm')
            store.update(job,stage='building');expect(page.locator('.thinking')).to_contain_text('正在建模',timeout=15000)
            expect(page.locator('#prompt')).to_have_value('将厚度改为 8 mm')
            page.screenshot(path=str(screens/'12-chat-progress-mobile.png'),full_page=True)
            finish(job);expect(page.locator('.model-thumbnail img')).to_be_visible(timeout=30000)
            expect(page.locator('#generate')).to_be_enabled();page.screenshot(path=str(screens/'13-chat-result-mobile.png'),full_page=True)
            page.locator('[data-preview]').click();expect(page.locator('#preview-viewer canvas')).to_be_visible(timeout=30000)
            page.locator('#preview-drawings').click();expect(page.locator('#preview-projections img')).to_have_count(3)
            expect(page.locator('#preview-drawings')).to_have_attribute('aria-selected','true')
            expect(page.locator('#preview-dialog[open]')).to_have_count(1)
            page.screenshot(path=str(screens/'16-dark-projections-mobile.png'),full_page=True)
            page.locator('#preview-model').click();page.screenshot(path=str(screens/'14-chat-preview-mobile.png'),full_page=True)
            page.locator('#confirm-model').click();expect(page.locator('[data-download="model.stl"]')).to_be_visible()
            with page.expect_download() as dl:page.locator('[data-download="model.stl"]').click()
            assert Path(dl.value.path()).read_bytes()==fixture.read_bytes()
            page.keyboard.press('Escape');expect(page.locator('#preview-dialog')).not_to_be_visible()
            page.reload();expect(page.locator('.model-thumbnail img')).to_be_visible(timeout=30000)
            expect(page.locator('#prompt')).to_have_value('将厚度改为 8 mm')
            with page.expect_response(lambda r:r.url==origin+'/api/jobs' and r.request.method=='POST') as response:page.locator('#generate').click()
            second=response.value.json();assert second['parent']==job and second['version']==2
            expect(page.locator('.user-message')).to_have_count(2);expect(page.locator('.user-message').last).to_have_text('将厚度改为 8 mm')
            store.update(second['id'],status='needs_input',stage='needs_input',plan=json.dumps({'questions':['孔径保持 6 mm 吗？']}))
            expect(page.locator('.assistant-message').last).to_contain_text('孔径保持',timeout=15000)
            page.reload();expect(page.locator('.user-message')).to_have_count(2)
            page.set_viewport_size({'width':1440,'height':1000});page.screenshot(path=str(screens/'15-chat-history-desktop.png'),full_page=True)
            expect(page.locator('.chat-sidebar')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.goto(origin+'/#create');page.locator('#demo').click();expect(page.locator('.thinking')).to_be_visible()
            page.locator('[data-cancel]').click();expect(page.locator('.assistant-message')).to_contain_text('任务已取消')
            page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.goto(origin+'/#settings');page.locator('#add-provider').click();expect(page.locator('#provider-form')).to_be_visible()
            assert not errors,errors
            browser.close()
        print('PASS chat: home, upload, submit, live phases, draft preservation, true STL thumbnail, modal, projections, confirm/download, refresh, versions, questions, cancel, mobile/desktop, settings')
    finally:
        client.close();server.should_exit=True;thread.join(timeout=10)
