// Cache the shell only. Private API responses, models, keys and uploads never enter Cache Storage.
const CACHE='zaowu-shell-v8';
const SHELL=['/','/static/style.css','/static/theme.css','/static/app.js','/static/chat.js','/static/provider-ui.js','/static/viewer.js','/static/vendor/three.module.js','/static/vendor/three.core.js','/static/vendor/STLLoader.js','/static/vendor/OrbitControls.js'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL))));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))));
self.addEventListener('fetch',e=>{if(e.request.method!=='GET'||new URL(e.request.url).origin!==location.origin)return;if(!SHELL.includes(new URL(e.request.url).pathname))return;e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))});
