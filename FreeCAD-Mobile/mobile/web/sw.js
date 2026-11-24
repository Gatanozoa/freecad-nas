// Cache the shell only. Private API responses, models, keys and uploads never enter Cache Storage.
const CACHE='zaowu-shell-v16';
const SHELL=['/','/static/style.css','/static/theme.css','/static/app.js','/static/chat.js','/static/chat-ui.js','/static/preview.js','/static/measurement-ui.js','/static/viewport.js','/static/provider-ui.js','/static/viewer.js','/static/vendor/three.module.js','/static/vendor/three.core.js','/static/vendor/STLLoader.js','/static/vendor/OrbitControls.js'];
for(const name of ['attach','collapse','connection-warning','expand','new-conversation','retry-muted','retry','send','step-complete'])SHELL.push('/static/icons/'+name+'.svg');
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL.map(url=>new Request(url,{cache:'reload'}))))));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('message',e=>{if(e.data==='ACTIVATE_UPDATE')self.skipWaiting()});
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=='GET'||url.origin!==location.origin||!SHELL.includes(url.pathname))return;
  event.respondWith((async()=>{
    let response;
    try{response=await fetch(event.request,{cache:'no-cache'})}catch{}
    if(response&&response.status<500)return response;
    return await caches.match(url.pathname)||response||Response.error();
  })());
});
