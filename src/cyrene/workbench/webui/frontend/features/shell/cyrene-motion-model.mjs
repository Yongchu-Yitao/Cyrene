import { CYRENE_STYLES, cyreneTargetPose } from './cyrene-motion-poses.mjs';
import { createCyreneIdleMotion } from './cyrene-idle-motion.mjs';

export const CYRENE_STATES = ['idle', 'thinking', 'tool', 'replying', 'waiting', 'done', 'error', 'stopped', 'reconnecting'];
const marks = ['waiting', 'done', 'error', 'stopped', 'reconnecting'];
const terminal = new Set(['done', 'error', 'stopped']);
const neutral = () => ({x:0,y:0,r:0,sx:1,sy:1,ex:0,ey:0,blink:1,leftEye:1,rightEye:1,glow:0,angle:0,
  waiting:0,done:0,error:0,stopped:0,reconnecting:0});

export function createCyreneMotion({ random = Math.random, idleRandom = Math.random } = {}) {
  const pose=neutral(), velocity=Object.fromEntries(Object.keys(pose).map(key=>[key,0]));
  const bags=new Map(), previous=new Map();
  // Attention has its own random stream so it cannot alter other states' choices.
  const idle=createCyreneIdleMotion({random:idleRandom});
  let idleAngle=0;
  let state='idle', style='', phase=0, age=0, angle=0, pulse=0, held=false, hover=false, reduced=false, nextStyle=0;
  function pick() {
    let bag=bags.get(state);
    if (!bag?.length) {
      bag=Object.keys(CYRENE_STYLES);
      for(let i=bag.length-1;i>0;i--) { const j=Math.floor(random()*(i+1)); [bag[i],bag[j]]=[bag[j],bag[i]]; }
      if(bag.at(-1)===previous.get(state)) [bag[0],bag[bag.length-1]]=[bag.at(-1),bag[0]];
      bags.set(state,bag);
    }
    style=bag.pop(); previous.set(state,style); age=0; nextStyle=6+random()*4;
    // Only change the target: keep rendered position AND velocity untouched.
    angle=Math.round(angle/360)*360;
    idleAngle=0;
  }
  pick();
  function staticPose() {
    Object.assign(pose,neutral());
    for(const mark of marks)pose[mark]=state===mark?1:0;
    if(state==='stopped')pose.blink=.5;
    if(state==='tool'||state==='reconnecting')pose.glow=.75;
    for(const key of Object.keys(velocity))velocity[key]=0;
  }
  return {
    pose, velocity,
    get state(){return state;}, get style(){return style;}, get phase(){return phase;},
    get attention(){return state==='idle'?idle.attention:null;},
    get resting(){return reduced || (terminal.has(state)&&age>4&&!held&&!hover&&pulse<.001&&Object.values(velocity).every(value=>Math.abs(value)<.005));},
    setState(value){const next=CYRENE_STATES.includes(value)?value:'idle';if(next===state)return;state=next;idle.clearTargets();pick();if(reduced)staticPose();},
    setReduced(value){reduced=!!value;if(reduced){held=false;hover=false;pulse=0;angle=0;idleAngle=0;idle.clearTargets();staticPose();}},
    setAttentionTargets(targets){if(state==='idle'&&!reduced)idle.setTargets(targets);},
    noticeInput(){if(state==='idle'&&!reduced)idle.noticeInput();},
    setHover(value){hover=!reduced&&!!value;},
    press(){if(!reduced)held=true;},
    release(){if(!held)return;held=false;pulse=Math.min(1.6,pulse+.95);if(style==='orbit')angle+=150;},
    cancelPress(){held=false;hover=false;},
    step(seconds){
      if(reduced)return pose;
      const dt=Math.max(0,Math.min(.05,Number(seconds)||0));
      if(!dt)return pose;
      phase+=dt;age+=dt;nextStyle-=dt;pulse*=Math.exp(-dt*6);
      if(nextStyle<=0&&!terminal.has(state)&&!held)pick();
      const cfg=CYRENE_STYLES[style];
      let target;
      if(state==='idle'){
        target=idle.step(dt,style,phase);
        const offset=target.angle;target.angle=angle+offset-idleAngle;idleAngle=offset;
      }else target=cyreneTargetPose(state,style,phase,age,angle,dt);
      angle=target.angle;
      target.y+=(held?1.4:0)-pulse*cfg.jump-(hover?.35:0);
      target.r+=style==='playful'?pulse*14:0;
      target.sx+=(held?cfg.press:0);target.sy-=(held?cfg.press:0);
      if(held)target.blink=.3;
      for(const mark of marks)target[mark]=state===mark?1:0;
      const steps=Math.max(1,Math.ceil(dt*120)),h=dt/steps;
      for(let i=0;i<steps;i++)for(const key of Object.keys(pose)){
        const eye=state==='idle'&&(key==='ex'||key==='ey');
        velocity[key]+=((eye?380:cfg.k)*(target[key]-pose[key])-(eye?30:cfg.d)*velocity[key])*h;
        pose[key]+=velocity[key]*h;
      }
      return pose;
    },
  };
}
