"""Vendor a fixed official Three.js distribution; no runtime CDN dependency."""
import bootstrap
from pathlib import Path
import urllib.request
import json
import tarfile
import io
import hashlib
import base64

root=bootstrap.ROOT/'mobile/web/vendor';root.mkdir(parents=True,exist_ok=True)
version='0.180.0'
meta=json.load(urllib.request.urlopen('https://registry.npmjs.org/three/'+version,timeout=40))
raw=urllib.request.urlopen(meta['dist']['tarball'],timeout=90).read()
expected=meta['dist']['integrity'].removeprefix('sha512-')
assert base64.b64encode(hashlib.sha512(raw).digest()).decode()==expected
mapping={'build/three.module.js':'three.module.js','build/three.core.js':'three.core.js','examples/jsm/loaders/STLLoader.js':'STLLoader.js','examples/jsm/controls/OrbitControls.js':'OrbitControls.js','LICENSE':'LICENSE'}
with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
    for source,target in mapping.items():
        (root/target).write_bytes(archive.extractfile('package/'+source).read())
(root/'VERSION').write_text(version,encoding='utf-8')
from PIL import Image,ImageDraw
for size in (192,512):
    image=Image.new('RGB',(size,size),'#176b5b'); draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((size*.24,size*.27,size*.76,size*.73),radius=size*.08,fill='#e7f0e3')
    draw.ellipse((size*.44,size*.44,size*.56,size*.56),fill='#176b5b')
    image.save(bootstrap.ROOT/f'mobile/web/icon-{size}.png')
print('Official Three.js '+version+' vendored; SHA-512 integrity checked; icons generated')
