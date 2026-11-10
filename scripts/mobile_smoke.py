"""Validate the built image without cloud, NAS, or private configuration."""
import os
import sys
from pathlib import Path
sys.path.insert(0, '/opt/mobile')
from cryptography.fernet import Fernet
from starlette.testclient import TestClient
from mobile.api import create_app
from mobile.store import Store
from mobile.worker import DEMO
from mobile.script_contract import prepare_script

assert os.getuid()==10001
os.environ['MASTER_KEY']=Fernet.generate_key().decode()
os.environ['DATA_DIR']='/data'
store=Store()
with TestClient(create_app(store)) as client:
    assert client.get('/').status_code==200
    for name in ('app.js','chat.js','viewer.js','provider-ui.js','style.css','vendor/three.module.js','vendor/three.core.js'):
        assert client.get('/static/'+name).status_code==200,name
    assert client.post('/api/login',json={'username':'admin','password':'admin'}).status_code==200
    result=client.post('/api/jobs',json={'prompt':'Image smoke test','demo':True})
    assert result.status_code==202,result.text
    job=result.json()
    assert client.get('/api/jobs/'+job['id']).json()['status']=='pending'
    assert client.post('/api/jobs/'+job['id']+'/cancel',json={}).json()['status']=='cancelled'
assert 'result' in prepare_script(DEMO)
assert not Path('/opt/mobile/.env').exists()
print('Image verified: non-root, read-only root, writable state, frontend, login, queue and orchestrator imports')
