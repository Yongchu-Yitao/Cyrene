export const CYRENE_STYLES = {soft:{k:200,d:29,press:.09,jump:1.5,tilt:5},snappy:{k:330,d:24,press:.12,jump:2.2,tilt:8},orbit:{k:190,d:26,press:.08,jump:1,tilt:3},playful:{k:210,d:18,press:.16,jump:3.1,tilt:14}};

// Approved poses; numerical springs in the model bridge every transition.
export function cyreneTargetPose(state, style, phase, stateAge, angle, dt) {
const cfg=CYRENE_STYLES[style];
let x=0,y=0,r=0,ex=0,ey=0,sx=1,sy=1,blink=1,leftEye=1,rightEye=1,glow=0;
const wave=Math.sin(phase*3);
if(state==='idle'){y=-.8*Math.sin(phase*2);sx=sy=1+.03*Math.sin(phase*2);if(phase%4.2>4.02)blink=.08;}
if(state==='thinking'){r=Math.max(10,cfg.tilt)*wave;x=.9*wave;ex=1.8*wave;ey=-1;y=-1.2*Math.cos(phase*3);}
if(state==='tool'){angle+=dt*(style==='orbit'?230:180);y=-1.5*Math.sin(phase*5);sx=1+.04*Math.sin(phase*5);sy=2-sx;glow=.95;if(phase%2.4>2.25)blink=.1;}
if(state==='replying'){y=-Math.max(2.6,cfg.jump)*Math.sin(phase*7);sy=1+.1*Math.sin(phase*7);sx=2-sy;leftEye=.825+.425*Math.sin(phase*14);rightEye=.825-.425*Math.sin(phase*14);}
if(state==='waiting'){r=-12*(.5+.5*Math.sin(phase*2.3));ey=-1;y=-1.3*Math.sin(phase*4.6);}
if(state==='done'){y=-7*Math.sin(stateAge*6)*Math.exp(-stateAge*2.3);sx=1+.14*Math.cos(stateAge*8)*Math.exp(-stateAge*3);sy=2-sx;r=-12*Math.sin(stateAge*5)*Math.exp(-stateAge*3);blink=.7;}
if(state==='error'){r=12*Math.sin(stateAge*18)*Math.exp(-stateAge*3);x=2.5*Math.sin(stateAge*18)*Math.exp(-stateAge*3);blink=.6;}
if(state==='stopped')blink=.5;
if(state==='reconnecting'){angle+=dt*100*Math.cos(phase*2);r=4*wave;glow=.55+.4*Math.sin(phase*3);}

const decay=Math.exp(-stateAge*2.4),beat=Math.sin(phase*4);
if(style==='soft'){
 if(state==='idle'){r=4*wave;y=0;sx=sy=1;blink=phase%4.5>4.1&&phase%4.5<4.25||phase%4.5>4.35?.1:1;}
 if(state==='thinking'){x=0;r=0;y=1.6*Math.sin(phase*4);ey=-1.4*Math.sin(phase*4);ex=0;sx=1+.04*beat;sy=2-sx;}
 if(state==='tool'){angle-=dt*180;y=0;sx=sy=1+.09*Math.sin(phase*7);glow=.65+.3*Math.sin(phase*7);leftEye=rightEye=.75;}
 if(state==='replying'){y=1.9*beat;r=0;sx=1;sy=1+.06*beat;leftEye=rightEye=.8+.4*Math.sin(phase*8);}
 if(state==='waiting'){r=0;y=-1.5*beat;sx=1+.05*beat;sy=1-.05*beat;ey=-1;blink=phase%3>2.8?.15:1;}
 if(state==='done'){r=0;y=5*Math.sin(stateAge*12)*decay;sx=1;sy=1+.12*Math.sin(stateAge*12)*decay;}
 if(state==='error'){x=0;r=0;y=-3*Math.sin(stateAge*5)*decay;sx=sy=1-.18*Math.sin(stateAge*5)*decay;}
 if(state==='stopped'){y=3*Math.sin(stateAge*5)*decay;ey=.4;blink=.7;}
 if(state==='reconnecting'){angle-=dt*100*Math.cos(phase*2);r=0;sx=1+.09*Math.sin(phase*3);sy=2-sx;y=-1.5*Math.sin(phase*3);glow=.7;}
}
if(style==='orbit'){
 if(state==='idle'){angle+=dt*16*Math.cos(phase*1.2);y=0;sx=sy=1;glow=.25+.2*Math.sin(phase*2);}
 if(state==='thinking'){angle+=dt*65*Math.cos(phase*2.3);r=0;x=0;y=0;ex=1.7*Math.sin(phase*2.3);ey=-1.2*Math.cos(phase*2.3);glow=.6;}
 if(state==='tool'){angle+=dt*60;y=-.7*Math.sin(phase*4);sx=sy=1;glow=.95;}
 if(state==='replying'){angle+=dt*70*Math.cos(phase*5);y=0;sx=sy=1;leftEye=.8+.4*Math.sin(phase*10);rightEye=.8+.4*Math.sin(phase*10-1);glow=.6;}
 if(state==='waiting'){angle+=dt*20*Math.cos(phase*1.5);r=0;y=0;ey=-1.5;glow=.4;}
 if(state==='done'){angle+=dt*864*Math.exp(-stateAge*2.4);y=0;r=0;sx=sy=1;blink=.7;glow=decay;}
 if(state==='error'){angle+=dt*160*Math.cos(stateAge*7)*decay;r=0;x=0;y=0;glow=.5*decay;}
 if(state==='stopped'){angle+=dt*80*decay;ey=0;blink=.85;}
 if(state==='reconnecting'){angle-=dt*100*Math.cos(phase*2);angle+=dt*80;r=0;y=0;glow=.85;}
}
if(style==='playful'){
 if(state==='idle'){r=6*Math.sin(phase*1.7);y=-.6*Math.sin(phase*3.4);leftEye=phase%4>3.7?.1:1;blink=1;}
 if(state==='thinking'){r=14*Math.sin(phase*2);x=0;y=-1.1*Math.sin(phase*4);ex=2*Math.sin(phase*2);ey=-1.5*Math.abs(Math.cos(phase*2));leftEye=.8+.25*Math.sin(phase*4);}
 if(state==='tool'){angle-=dt*180;r=8*Math.sin(phase*7);x=1.3*Math.sin(phase*7);y=-1.2*Math.abs(Math.sin(phase*7));leftEye=.6;rightEye=.9;glow=.9;}
 if(state==='replying'){r=9*Math.sin(phase*6);x=1.4*Math.sin(phase*6);y=-2.8*Math.abs(Math.sin(phase*6));leftEye=.7+.5*Math.sin(phase*12);rightEye=.7+.5*Math.cos(phase*12);}
 if(state==='waiting'){r=9*Math.sin(phase*2);ex=1.8*Math.sin(phase*2);y=-1.5*Math.abs(Math.sin(phase*2));blink=phase%3>2.6?.15:1;}
 if(state==='done'){r=25*Math.sin(stateAge*7)*decay;y=-5*Math.abs(Math.sin(stateAge*8))*decay;leftEye=stateAge<.6?.1:1;rightEye=1;blink=1;}
 if(state==='error'){x=2*Math.sin(stateAge*24)*decay;r=7*Math.sin(stateAge*24)*decay;blink=stateAge<.5?.15:.7;}
 if(state==='stopped'){r=-9;ey=1.3;y=1;blink=.45;}
 if(state==='reconnecting'){angle-=dt*100*Math.cos(phase*2);r=11*Math.sin(phase*2.8);ex=2.2*Math.sin(phase*2.8);ey=-1;y=-1.6*Math.abs(Math.sin(phase*2.8));glow=.8;}
}
return {x,y,r,sx,sy,ex,ey,blink,leftEye,rightEye,glow,angle};
}
