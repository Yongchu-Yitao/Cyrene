import { createGripLogoMood } from './grip-logo-mood.mjs';
// A spring moves the outer slot; the inner SVG keeps its own activity animation.
export function createGripLogoDrag(slot, start, win = window) {
  const rect = slot.getBoundingClientRect(), original = slot.style.transform;
  const reduced = () => win.matchMedia('(prefers-reduced-motion: reduce)').matches
    || slot.ownerDocument.documentElement.dataset.performanceMode === 'on';
  const pane = slot.closest?.('.wbc-pane-card')?.getBoundingClientRect();
  const bounds = pane || {left:0,top:0,right:win.innerWidth,bottom:win.innerHeight};
  const clamp = (n, low, high) => Math.max(low, Math.min(high, n));
  let x=0,y=0,vx=0,vy=0,tx=0,ty=0,last=null,frame=null,released=false,disposed=false;
  const expression=createGripLogoMood();
  let clock=0;
  let phase=0,lookX=0,lookY=0,effort=0;
  function reset() {
    slot.style.transform=original;
    delete slot.dataset.logoDragging;
    delete slot.dataset.logoMood;
    for(const name of ['--logo-drag-look-x','--logo-drag-look-y','--logo-drag-squint','--logo-drag-left-eye','--logo-drag-right-eye'])slot.style.removeProperty(name);
  }
  function paint(dt) {
    const tension=Math.hypot(tx-x,ty-y), speed=Math.hypot(vx,vy);
    const tilt=clamp(vx*.025+(tx-x)*.08,-18,18);
    const stretch=clamp(speed/3000,0,.12);
    slot.style.transform=`translate(${x}px, ${y}px) rotate(${tilt}deg) scale(${1+stretch}, ${1-stretch*.55})`;
    phase+=dt;
    const ease=1-Math.exp(-dt*14);
    const pull=released?0:Math.hypot(tx,ty);
    effort+=(clamp(pull/115+tension/180+speed/1700,0,1)-effort)*ease;
    lookX+=(clamp(tx*.048+(tx-x)*.06,-2.8,2.8)-lookX)*ease;
    lookY+=(clamp(ty*.04+(ty-y)*.04,-2,2)-lookY)*ease;
    // Keep looking toward the pull even when held still; blink and unequal
    // eyelids make the expression react instead of freezing into a squint.
    const blink=released?1:1-.85*Math.exp(-(((phase%2.7-2.35)/.065)**2));
    const mood=released?'':expression.sample(clock);
    if(mood)slot.dataset.logoMood=mood;else delete slot.dataset.logoMood;
    const base=mood==='strain'?.58:1;
    const squint=base*blink;
    const asymmetry=clamp(lookX*(mood==='curious'?.13:.065),-.3,.3);
    slot.style.setProperty('--logo-drag-look-x',`${lookX}px`);
    slot.style.setProperty('--logo-drag-look-y',`${lookY}px`);
    slot.style.setProperty('--logo-drag-squint',String(squint));
    slot.style.setProperty('--logo-drag-left-eye',String(clamp(squint-asymmetry,.1,1.15)));
    slot.style.setProperty('--logo-drag-right-eye',String(clamp(squint+asymmetry,.1,1.15)));
  }
  function tick(time) {
    frame=null;if(disposed)return;
    const dt=last===null?1/60:Math.min(.032,Math.max(0,(time-last)/1000));last=time;clock+=dt*1000;
    if(reduced()){x=tx;y=ty;vx=vy=0;slot.style.transform=`translate(${x}px, ${y}px)`;}
    else {
      // Substeps keep the spring stable even after a slow rendering frame.
      const steps=Math.max(1,Math.ceil(dt*120)),h=dt/steps;
      for(let i=0;i<steps;i++){
        vx+=(170*(tx-x)-17*vx)*h;vy+=(170*(ty-y)-17*vy)*h;
        x+=vx*h;y+=vy*h;
      }
      const limitedX=clamp(x,Math.max(-48,Math.min(0,bounds.left+8-rect.left)),Math.min(48,Math.max(0,bounds.right-rect.right-8)));
      const limitedY=clamp(y,Math.max(-36,Math.min(0,bounds.top+8-rect.top)),Math.min(36,Math.max(0,bounds.bottom-rect.bottom-8)));
      if(limitedX!==x){x=limitedX;vx=0;}if(limitedY!==y){y=limitedY;vy=0;}
      paint(dt);
    }
    if(released&&Math.hypot(x,y)<.15&&Math.hypot(vx,vy)<.5){reset();last=null;return;}
    if((!released&&!reduced())||Math.hypot(tx-x,ty-y)>.05||Math.hypot(vx,vy)>.1)wake();else last=null;
  }
  function wake(){if(frame===null&&!disposed)frame=win.requestAnimationFrame(tick);}
  function move(event) {
    if(released||disposed)return;
    const dx=event.clientX-start.clientX,dy=event.clientY-start.clientY;
    // Radial resistance: pulling further never pins the logo to the pointer.
    expression.update(dx,dy,clock);
    const pullX=dx*.35,pullY=dy*.35;
    const resistance=1/Math.sqrt(1+(pullX/48)**2+(pullY/36)**2);
    tx=clamp(pullX*resistance,Math.min(0,bounds.left+8-rect.left),Math.max(0,bounds.right-rect.right-8));
    ty=clamp(pullY*resistance,Math.min(0,bounds.top+8-rect.top),Math.max(0,bounds.bottom-rect.bottom-8));
    slot.dataset.logoDragging='true';wake();
  }
  function finish(){if(released||disposed)return;released=true;delete slot.dataset.logoMood;tx=ty=0;unlisten();wake();}
  function release(event){if(event.pointerId===undefined||event.pointerId===start.pointerId)finish();}
  function unlisten(){win.removeEventListener('blur',finish);win.removeEventListener('pointerup',release,true);win.removeEventListener('pointercancel',release,true);}
  win.addEventListener('blur',finish);
  win.addEventListener('pointerup',release,true);
  win.addEventListener('pointercancel',release,true);
  return {move,finish,destroy(){disposed=true;unlisten();if(frame!==null)win.cancelAnimationFrame(frame);reset();}};
}
