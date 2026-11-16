// Android keyboards may shrink only visualViewport, leaving 100dvh unchanged.
export function trackChatViewport(host){
  const body=document.body, viewport=window.visualViewport;
  const mobile=window.matchMedia('(max-width:800px)');
  let frame=0,baseline=window.innerHeight,lastWidth=window.innerWidth;
  function update(){
    frame=0;
    const enabled=mobile.matches;
    body.classList.toggle('chat-viewport',enabled);
    if(!enabled){body.classList.remove('keyboard-open');return}
    // Pinch zoom is not keyboard activity; preserve browser zoom/panning behavior.
    if(viewport&&Math.abs(viewport.scale-1)>.05)return;
    const height=viewport?.height||window.innerHeight;
    const focused=host.querySelector('#prompt')===document.activeElement;
    if(Math.abs(window.innerWidth-lastWidth)>80){baseline=height;lastWidth=window.innerWidth}
    if(!focused)baseline=Math.max(window.innerHeight,height);
    body.style.setProperty('--chat-visible-height',height+'px');
    body.style.setProperty('--chat-visible-top',(viewport?.offsetTop||0)+'px');
    body.classList.toggle('keyboard-open',focused&&baseline-height>120);
  }
  function schedule(){if(!frame)frame=requestAnimationFrame(update)}
  for(const target of [window,viewport].filter(Boolean)){
    target.addEventListener('resize',schedule);target.addEventListener('scroll',schedule);
  }
  host.addEventListener('focusin',schedule);host.addEventListener('focusout',schedule);
  mobile.addEventListener('change',schedule);update();
  return ()=>{
    cancelAnimationFrame(frame);
    for(const target of [window,viewport].filter(Boolean)){
      target.removeEventListener('resize',schedule);target.removeEventListener('scroll',schedule);
    }
    host.removeEventListener('focusin',schedule);host.removeEventListener('focusout',schedule);
    mobile.removeEventListener('change',schedule);
    body.classList.remove('chat-viewport','keyboard-open');
    body.style.removeProperty('--chat-visible-height');body.style.removeProperty('--chat-visible-top');
  };
}
