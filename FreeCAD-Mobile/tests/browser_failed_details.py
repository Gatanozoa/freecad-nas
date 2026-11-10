import bootstrap
import os
import socket
import tempfile
import threading
import time
from cryptography.fernet import Fernet
import uvicorn
from playwright.sync_api import sync_playwright,expect
from mobile.store import Store
from mobile.api import create_app

qa=bootstrap.ROOT/'.qa';qa.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=qa) as directory:
    os.environ['MASTER_KEY']=Fernet.generate_key().decode()
    sock=socket.socket();sock.bind(('127.0.0.1',0));origin='http://127.0.0.1:'+str(sock.getsockname()[1]);os.environ['PUBLIC_URL']=origin
    store=Store(directory)
    job=store.submit('失败页面验证',None,[],demo=True)
    script='import Part\nshape = Part.makeCylinder(50, 5)\n'
    store.update(job,status='failed',stage='failed',script=script,error='Define result as the final FreeCAD document object with Shape')
    server=uvicorn.Server(uvicorn.Config(create_app(store),log_level='critical',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    while not server.started:time.sleep(.05)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel=os.environ.get('BROWSER_CHANNEL') or None,headless=True)
            context=browser.new_context(viewport={'width':390,'height':844},accept_downloads=True)
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(origin);page.locator('[name=password]').fill('admin');page.locator('#login-form button').click();expect(page.locator('#main h1')).to_have_text('创建模型')
            page.goto(origin+'/#detail/'+job)
            summary=page.locator('summary').filter(has_text='高级信息')
            expect(summary).to_be_visible();summary.click();expect(page.locator('#source-code')).to_have_text(script)
            with page.expect_download() as download:page.locator('#download-source').click()
            assert download.value.suggested_filename=='model.py'
            assert open(download.value.path(),encoding='utf-8').read()==script
            expect(page.locator('#rename')).to_be_visible();expect(page.locator('#delete')).to_be_visible()
            page.screenshot(path=str(qa/'screens/10-failed-details-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000});expect(summary).to_be_visible();expect(page.locator('#source-code')).to_be_visible()
            assert not errors,errors
            browser.close()
        print('Failed task mobile/desktop: advanced information visible, script view/download exact, rename/delete visible; no model API or NAS calls')
    finally:server.should_exit=True;thread.join(timeout=10)
