import { createCyreneMotion } from './cyrene-motion-model.mjs';
import { attachCyreneIdleAttention } from './cyrene-idle-attention.mjs';

export function attachCyreneMotion(svg) {
  const motion=createCyreneMotion(), doc=svg.ownerDocument, win=doc.defaultView;
  const button=svg.closest('button');
  const find=name=>svg.querySelector('.cyrene-icon-'+name);
  const body=find('body'),ring=find('ring'),gaze=find('gaze'),left=find('eye'),right=find('eye-right'),glint=find('glint');
  const badges={waiting:find('wait'),done:find('done'),error:find('error'),stopped:find('pause'),reconnecting:find('reconnect')};
  const media=win.matchMedia('(prefers-reduced-motion: reduce)');
  const attention=attachCyreneIdleAttention(svg,motion);
  let frame=null,last=null,disposed=false,reduced=false,lastKeyboard=-Infinity;
  const removers=[];
  function listen(target,event,fn){target?.addEventListener(event,fn);removers.push(()=>target?.removeEventListener(event,fn));}
  function paint(){
    const p=motion.pose;
    svg.dataset.motionStyle=motion.style;
    if(motion.attention)svg.dataset.idleAttention=motion.attention;else delete svg.dataset.idleAttention;
    body.style.transform=`translate(${p.x}px,${p.y}px) rotate(${p.r}deg) scale(${p.sx},${p.sy})`;
    ring.style.transform=`rotate(${p.angle}deg)`;
    gaze.style.transform=`translate(${p.ex}px,${p.ey}px) scaleY(${Math.max(.08,p.blink)})`;
    left.style.transform=`scaleY(${Math.max(.08,p.leftEye)})`;
    right.style.transform=`scaleY(${Math.max(.08,p.rightEye)})`;
    glint.style.opacity=Math.max(0,Math.min(1,p.glow));
    glint.style.strokeDashoffset=reduced?-30:24-motion.phase*(motion.state==='idle'?25:105);
    for(const [mark,element] of Object.entries(badges)){
      const value=p[mark];element.style.opacity=Math.max(0,Math.min(1,value));
      element.style.transform=`translateY(${(1-value)*2}px) scale(${Math.max(.1,.6+.4*value)})`;
    }
  }
  function stop(){if(frame!==null)win.cancelAnimationFrame(frame);frame=null;last=null;}
  function tick(time){frame=null;if(disposed||doc.hidden||reduced)return;
    attention.sample();
    motion.step(last===null?0:(time-last)/1000);last=time;paint();
    if(!motion.resting)frame=win.requestAnimationFrame(tick);else last=null;
  }
  function wake(){if(!disposed&&!doc.hidden&&!reduced&&frame===null)frame=win.requestAnimationFrame(tick);}
  function preferences(){
    reduced=media.matches||doc.documentElement.dataset.performanceMode==='on';
    motion.setReduced(reduced);
    if(reduced){attention.clear();stop();paint();}else wake();
  }
  listen(media,'change',preferences);
  listen(win,'cyrene:performance-mode',preferences);
  const observer=new win.MutationObserver(preferences);
  observer.observe(doc.documentElement,{attributes:true,attributeFilter:['data-performance-mode']});
  listen(doc,'visibilitychange',()=>{motion.cancelPress();if(doc.hidden)stop();else wake();});
  listen(button,'pointerdown',event=>{if(event.button!==0)return;button.setPointerCapture?.(event.pointerId);motion.press();wake();});
  listen(button,'pointerup',()=>{motion.release();wake();});
  listen(button,'pointercancel',()=>{motion.cancelPress();wake();});
  listen(button,'lostpointercapture',()=>{motion.cancelPress();wake();});
  listen(button,'pointerenter',()=>{motion.setHover(true);wake();});
  listen(button,'pointerleave',()=>{motion.setHover(false);wake();});
  listen(button,'blur',()=>{motion.cancelPress();wake();});
  listen(button,'keydown',event=>{if(!['Enter',' '].includes(event.key)||event.repeat)return;lastKeyboard=win.performance.now();motion.press();wake();});
  listen(button,'keyup',event=>{if(!['Enter',' '].includes(event.key))return;lastKeyboard=win.performance.now();motion.release();wake();});
  listen(button,'click',event=>{if(event.detail===0&&win.performance.now()-lastKeyboard>250){motion.press();motion.release();wake();}});
  preferences();paint();wake();
  return {
    setState(state){if(state!==motion.state)attention.clear();motion.setState(state);if(reduced)paint();else wake();},
    destroy(){disposed=true;stop();attention.destroy();observer.disconnect();removers.forEach(remove=>remove());},
  };
}
