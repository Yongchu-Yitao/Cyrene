// Expressions must survive brief pointer reversals without flickering.
export function createGripLogoMood() {
  let mood='',candidate='',candidateAt=0,changedAt=-Infinity;
  let previousX=0,direction=0,reversals=0,lastReverse=-Infinity;
  return {
    update(dx,dy,time) {
      const step=dx-previousX;
      if(Math.abs(step)>3){
        const next=Math.sign(step);
        if(direction&&next!==direction){reversals=time-lastReverse<650?reversals+1:1;lastReverse=time;}
        direction=next;previousX=dx;
      }
      const next=reversals>=3&&time-lastReverse<900?'annoyed':Math.hypot(dx,dy)>110?'strain':'curious';
      if(next!==candidate){candidate=next;candidateAt=time;}
    },
    sample(time) {
      if(candidate!==mood&&time-candidateAt>=220&&time-changedAt>=900){mood=candidate;changedAt=time;}
      return mood;
    },
  };
}
