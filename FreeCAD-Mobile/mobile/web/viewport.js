// Keep a short geometry-only trace: never collect prompts, credentials or uploads.
const trace=[];
export function viewportReport(){
  const v=window.visualViewport,p=document.querySelector('#prompt'),r=p?.getBoundingClientRect();
  return JSON.stringify({client:'0.4.0',origin:location.origin,userAgent:navigator.userAgent,
    standalone:matchMedia('(display-mode:standalone)').matches,innerHeight,innerWidth,
    visual:v?{height:v.height,top:v.offsetTop,scale:v.scale}:null,
    prompt:r?{top:r.top,bottom:r.bottom}:null,events:trace},null,2);
}
export function trackChatViewport(host){
  const body=document.body,v=window.visualViewport,k=navigator.virtualKeyboard;
  const mobile=matchMedia('(max-width:800px)');
  let frame=0,timer=0,focusAt=0,baseline=innerHeight,lastWidth=innerWidth;
  function update(){
    frame=0;
    const enabled=mobile.matches;body.classList.toggle('chat-viewport',enabled);
    if(!enabled){body.classList.remove('keyboard-open','composer-editing','keyboard-fallback');return}
    const focused=host.querySelector('#prompt')===document.activeElement;
    // Zoom must not make keyboard handling stop entirely in standalone WebViews.
    const top=v?.offsetTop||0;
    let height=v?.height||innerHeight;
    if(k?.boundingRect?.height>0)height=Math.min(height,Math.max(1,k.boundingRect.y-top));
    if(Math.abs(innerWidth-lastWidth)>80){baseline=height;lastWidth=innerWidth}
    if(!focused)baseline=Math.max(innerHeight,height);
    const reported=baseline-height*(v?.scale||1)>120;
    const fallback=focused&&!reported&&performance.now()-focusAt>450;
    body.style.setProperty('--chat-visible-height',height+'px');
    body.style.setProperty('--chat-visible-top',top+'px');
    body.classList.toggle('composer-editing',focused);
    body.classList.toggle('keyboard-open',focused&&reported);
    // Some launchers overlay the keyboard without reporting a smaller viewport.
    // Move editing to the top rather than guessing an arbitrary keyboard height.
    body.classList.toggle('keyboard-fallback',fallback);
    const sample={height:Math.round(height),top:Math.round(top),layout:innerHeight,
      scale:v?.scale||1,focused,reported,fallback};
    if(JSON.stringify(sample)!==JSON.stringify(trace.at(-1))){trace.push(sample);if(trace.length>16)trace.shift()}
  }
  function schedule(){if(!frame)frame=requestAnimationFrame(update)}
  function focus(){focusAt=performance.now();clearTimeout(timer);timer=setTimeout(schedule,500);schedule()}
  for(const target of [window,v].filter(Boolean)){
    target.addEventListener('resize',schedule);target.addEventListener('scroll',schedule);
  }
  k?.addEventListener('geometrychange',schedule);
  host.addEventListener('focusin',focus);host.addEventListener('focusout',schedule);
  mobile.addEventListener('change',schedule);update();
  return ()=>{
    cancelAnimationFrame(frame);clearTimeout(timer);
    for(const target of [window,v].filter(Boolean)){
      target.removeEventListener('resize',schedule);target.removeEventListener('scroll',schedule);
    }
    k?.removeEventListener('geometrychange',schedule);
    host.removeEventListener('focusin',focus);host.removeEventListener('focusout',schedule);
    mobile.removeEventListener('change',schedule);
    body.classList.remove('chat-viewport','keyboard-open','composer-editing','keyboard-fallback');
    body.style.removeProperty('--chat-visible-height');body.style.removeProperty('--chat-visible-top');
  };
}
