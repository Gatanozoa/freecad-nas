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
    store=Store(directory);server=uvicorn.Server(uvicorn.Config(create_app(store),log_level='critical',access_log=False))
    with store.db() as db: db.execute('INSERT OR REPLACE INTO runtime VALUES(1,?)',(time.time()+120,))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    while not server.started:time.sleep(.05)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel=os.environ.get('BROWSER_CHANNEL') or None,headless=True)
            context=browser.new_context(viewport={'width':390,'height':844});page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.clock.install()
            page.goto(origin);page.locator('[name=password]').fill('admin');page.locator('#login-form button').click();expect(page.locator('#main h1')).to_have_text('创建模型')
            expect(page.locator('#connection')).to_have_text('任务后台在线')
            page.goto(origin+'/#settings')
            page.route('**/api/cad',lambda route:route.fulfill(json={'ready':False}))
            page.locator('#test-cad').click();expect(page.locator('#cad-status')).to_contain_text('暂未就绪')
            expect(page.locator('#connection')).to_have_text('任务后台在线')
            with store.db() as db: db.execute('UPDATE runtime SET heartbeat=0 WHERE id=1')
            page.clock.fast_forward(21000);expect(page.locator('#connection')).to_have_text('任务后台未就绪')
            with store.db() as db: db.execute('UPDATE runtime SET heartbeat=? WHERE id=1',(time.time()+120,))
            page.clock.fast_forward(21000);expect(page.locator('#connection')).to_have_text('任务后台在线')
            page.goto(origin+'/#settings');page.locator('#add-provider').click();expect(page.locator('#provider-form')).to_be_visible()
            page.locator('[name=name]').fill('我的图文模型');page.locator('[name=url]').fill('https://provider.test/v1/responses');page.locator('[name=model]').fill('custom-model');page.locator('[name=key]').fill('Bearer fake-key-for-test');page.locator('[name=vision]').check();page.locator('#save-provider').click()
            expect(page.locator('#provider-error')).to_contain_text('Chat Completions');expect(page.locator('#save-provider')).to_be_enabled()
            page.locator('[name=url]').fill('https://provider.test/v1/chat/completions');page.locator('#save-provider').click();expect(page.locator('#providers')).to_contain_text('我的图文模型')
            page.reload();expect(page.locator('#providers')).to_contain_text('我的图文模型');page.locator('[data-edit]').click();expect(page.locator('[name=key]')).to_have_value('')
            page.screenshot(path=str(qa/'screens/09-model-settings-mobile.png'),full_page=True)
            page.locator('[name=model]').fill('updated-model');page.locator('#save-provider').click();expect(page.locator('#providers')).to_contain_text('updated-model')
            page.goto(origin+'/#create');expect(page.locator('#provider')).to_have_value(store.default_provider());expect(page.locator('#provider')).to_contain_text('updated-model')
            local=page.evaluate('JSON.stringify(localStorage)');assert 'fake-key' not in local
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            browser.close()
        print('Mobile browser passed: independent backend/CAD status, periodic refresh, config and persistence; no cloud call')
    finally:server.should_exit=True;thread.join(timeout=10)
