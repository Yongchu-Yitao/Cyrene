import test from 'node:test';
import assert from 'node:assert/strict';
import { createCyreneMotion, CYRENE_STATES } from '../shell/cyrene-motion-model.mjs';
import { CYRENE_STYLES, cyreneTargetPose } from '../shell/cyrene-motion-poses.mjs';
const advance=(motion,seconds)=>{for(let i=0;i<seconds*120;i++)motion.step(1/120);};

test('every original state has four distinct approved poses',()=>{
  assert.equal(CYRENE_STATES.length,9);
  for(const state of CYRENE_STATES){
    const poses=Object.keys(CYRENE_STYLES).map(style=>JSON.stringify(cyreneTargetPose(state,style,.71,.35,0,1/60)));
    assert.equal(new Set(poses).size,4,state);
  }
});

test('shuffle bags cover every style before repeating and never repeat across bag boundaries',()=>{
  for(const state of CYRENE_STATES){
    const motion=createCyreneMotion({random:()=>0});const picks=[];
    for(let i=0;i<12;i++){
      motion.setState(state==='idle'?'thinking':'idle');motion.setState(state);picks.push(motion.style);
    }
    for(let i=0;i<picks.length;i++)if(i)assert.notEqual(picks[i],picks[i-1]);
    for(let i=0;i<12;i+=4)assert.equal(new Set(picks.slice(i,i+4)).size,4);
  }
});

test('live styles change after 6–10 seconds while terminal feedback plays once',()=>{
  for(const state of CYRENE_STATES){
    const m=createCyreneMotion({random:()=>.5});m.setState(state);const first=m.style;
    advance(m,5.5);assert.equal(m.style,first);
    advance(m,5);
    if(['done','error','stopped'].includes(state)){assert.equal(m.style,first);assert(m.resting,state);}
    else assert.notEqual(m.style,first,state);
  }
});

test('state changes and rapid clicks preserve position and velocity instead of resetting',()=>{
  const m=createCyreneMotion();m.setState('tool');advance(m,.7);
  for(const state of CYRENE_STATES){
    const pose={...m.pose},velocity={...m.velocity};
    m.setState(state);m.press();m.release();
    assert.deepEqual(m.pose,pose);assert.deepEqual(m.velocity,velocity);
    m.step(1/120);
    assert(Math.abs(m.pose.y-pose.y)<1,'no vertical jump');
    assert(Math.abs(m.pose.angle-pose.angle)<15,'no ring reset');
    for(const n of Object.values(m.pose))assert(Number.isFinite(n));
  }
});

test('reduced motion freezes movement but retains the correct status badge',()=>{
  const m=createCyreneMotion();m.setState('tool');advance(m,1);m.setReduced(true);
  for(const state of CYRENE_STATES){
    m.setState(state);const pose={...m.pose};m.press();m.release();advance(m,12);assert.deepEqual(m.pose,pose);
    if(['waiting','done','error','stopped','reconnecting'].includes(state))assert.equal(m.pose[state],1);
    assert(m.resting);
  }
  m.setReduced(false);m.setState('thinking');const frozen={...m.pose};advance(m,.3);assert.notDeepEqual(m.pose,frozen);
});

test('repeated token state updates do not reselect or restart the animation',()=>{
  const a=createCyreneMotion({random:()=>.3}),b=createCyreneMotion({random:()=>.3});
  a.setState('replying');b.setState('replying');
  for(let i=0;i<800;i++){a.setState('replying');a.step(1/120);b.step(1/120);}
  assert.equal(a.style,b.style);assert.deepEqual(a.pose,b.pose);
});
