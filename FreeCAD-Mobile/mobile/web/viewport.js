// Keep a short geometry-only trace: never collect prompts, credentials or uploads.
const trace=[];

export function viewportReport(){
  const v=window.visualViewport;
  const editing=document.querySelector('#keyboard-editor:not([hidden]) textarea');
  const input=editing||document.querySelector(matchMedia('(max-width:800px)').matches?'#edit-prompt':'#prompt');
  const r=input?.getBoundingClientRect();
  return JSON.stringify({client:'0.5.3',origin:location.origin,userAgent:navigator.userAgent,
    standalone:matchMedia('(display-mode:standalone)').matches,innerHeight,innerWidth,
    visual:v?{height:v.height,top:v.offsetTop,scale:v.scale}:null,
    prompt:r?{top:r.top,bottom:r.bottom}:null,events:trace},null,2);
}

export function trackChatViewport(editor,closeEditor){
  const body=document.body, visual=window.visualViewport, keyboard=navigator.virtualKeyboard;
  const mobile=matchMedia('(max-width:800px)');
  let frame=0,restoreTimer=0,baseline=innerHeight,lastWidth=innerWidth,wasReported=false;

  function update(){
    frame=0;
    const editing=!editor.hidden;
    body.classList.toggle('chat-viewport',mobile.matches);
    if(!mobile.matches){
      if(editing)closeEditor();
      return;
    }
    const top=visual?.offsetTop||0, scale=visual?.scale||1;
    let height=Math.min(visual?.height||innerHeight,innerHeight);
    const rect=keyboard?.boundingRect;
    if(rect?.height>0)height=Math.min(height,Math.max(1,rect.y-top));
    if((!editing&&!rect?.height)||Math.abs(innerWidth-lastWidth)>80){
      baseline=Math.max(innerHeight,height);
      lastWidth=innerWidth;
    }
    const reported=editing&&(rect?.height>0||baseline-height*scale>100);
    if(reported)wasReported=true;
    if(!editing)wasReported=false;
    if(editing&&wasReported&&!reported){
      if(!restoreTimer)restoreTimer=setTimeout(()=>{
        restoreTimer=0;
        closeEditor();
      },160);
    }else{
      clearTimeout(restoreTimer);
      restoreTimer=0;
    }

    body.style.setProperty('--chat-visible-height',height+'px');
    body.style.setProperty('--chat-visible-top',top+'px');
    // Unreported overlays leave the full layout viewport intact.
    // Reserve a lower portion for the keyboard while keeping the editor out of the message flow.
    editor.classList.toggle('keyboard-unmeasured',editing&&!reported&&!wasReported);
    const sample={height:Math.round(height),top:Math.round(top),layout:innerHeight,
      scale,editing,reported:!!reported};
    if(JSON.stringify(sample)!==JSON.stringify(trace.at(-1))){
      trace.push(sample);
      if(trace.length>16)trace.shift();
    }
  }

  function schedule(){if(!frame)frame=requestAnimationFrame(update)}
  for(const target of [window,visual].filter(Boolean)){
    target.addEventListener('resize',schedule);
    target.addEventListener('scroll',schedule);
  }
  keyboard?.addEventListener('geometrychange',schedule);
  mobile.addEventListener('change',schedule);
  update();
  return {schedule,dispose(){
    cancelAnimationFrame(frame);
    clearTimeout(restoreTimer);
    for(const target of [window,visual].filter(Boolean)){
      target.removeEventListener('resize',schedule);
      target.removeEventListener('scroll',schedule);
    }
    keyboard?.removeEventListener('geometrychange',schedule);
    mobile.removeEventListener('change',schedule);
    body.classList.remove('chat-viewport');
    body.style.removeProperty('--chat-visible-height');
    body.style.removeProperty('--chat-visible-top');
  }};
}
