// Animate the actual page surface. The canvas carries only the ink on the turning verso.
export function curlPage(stage, leaf, under, reverse, book, chapter, number) {
  const w = stage.clientWidth, h = stage.clientHeight;
  const canvas = document.createElement('canvas');
  canvas.className = 'wbc-folio-curl';
  canvas.setAttribute('aria-hidden', 'true');
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.ceil(w * scale);
  canvas.height = Math.ceil(h * scale);
  const ctx = canvas.getContext('2d');
  if (!ctx || !w || !h) return () => {};
  stage.append(canvas);
  ctx.scale(scale, scale);
  const paper = document.createElement('canvas');
  paper.width = canvas.width;
  paper.height = canvas.height;
  const ink = paper.getContext('2d');
  ink.scale(scale, scale);
  ink.fillStyle = '#e7e5d9';
  ink.fillRect(0, 0, w, h);
  ink.fillStyle = '#646d60';
  ink.font = '13px system-ui';
  ink.fillText(book.title, 22, 35, w - 44);
  ink.fillRect(22, 49, w - 44, 1);
  ink.font = '22px Georgia, serif';
  ink.fillText(chapter, 22, 91, w - 44);
  ink.font = '14px system-ui';
  ink.fillText(book.subtitle, 22, 120, w - 44);
  ink.fillText(String(number).padStart(2, '0'), w - 45, h - 24);
  const rect = [[0,0],[w,0],[w,h],[0,h]];
  const start = performance.now();
  let frame;
  function cleanup() {
    cancelAnimationFrame(frame);
    canvas.remove();
    for (const el of [leaf, under]) {
      el.style.removeProperty('clip-path');
      el.style.removeProperty('z-index');
      el.style.removeProperty('visibility');
    }
  }
  function draw(now) {
    const f = Math.min(1, (now - start) / 730);
    const u = reverse ? 1-f : f, t = u*u*(3-2*u);
    const angle = .4*(1-t)+.055, nx=Math.cos(angle), ny=Math.sin(angle);
    const radius = Math.min(w*.057,h*.145)*(.32+.68*Math.sin(Math.PI*t));
    const bend=Math.PI*radius, axis=(w*nx+h*ny)*(1-t)-t*(bend+3);
    const dist=([x,y])=>x*nx+y*ny-axis;
    function clip(poly,limit,side) {
      const out=[];
      poly.forEach((p,i)=>{
        const prev=poly[(i+poly.length-1)%poly.length], a=dist(prev)-limit, b=dist(p)-limit;
        if ((a*side>=0)!==(b*side>=0)) {
          const k=a/(a-b);
          out.push([prev[0]+(p[0]-prev[0])*k,prev[1]+(p[1]-prev[1])*k]);
        }
        if (b*side>=0) out.push(p);
      });
      return out;
    }
    function project(p) {
      const d=Math.max(0,dist(p)), theta=Math.min(Math.PI,d/radius);
      const offset=radius*Math.sin(theta)-Math.max(0,d-bend)-d;
      return [p[0]+offset*nx,p[1]+offset*ny-radius*(1-Math.cos(theta))*.19];
    }
    function band(lo,hi,color,printed) {
      const poly=clip(clip(rect,lo-.55,1),hi+.55,-1);
      if (poly.length<3) return;
      ctx.beginPath();
      poly.map(project).forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));
      ctx.closePath(); ctx.fillStyle=color; ctx.fill();
      if (!printed) return;
      const point=d=>[nx*(axis+d),ny*(axis+d)];
      const A=point(lo),P=project(A),Q=project(point(hi));
      const vx=(Q[0]-P[0])/(hi-lo),vy=(Q[1]-P[1])/(hi-lo);
      const a=vx*nx+ny*ny,b=vy*nx-nx*ny,c=vx*ny-nx*ny,d=vy*ny+nx*nx;
      ctx.save();ctx.clip();ctx.transform(a,b,c,d,P[0]-a*A[0]-c*A[1],P[1]-b*A[0]-d*A[1]);
      ctx.translate(w,0);ctx.scale(-1,1);ctx.drawImage(paper,0,0,w,h);ctx.restore();
    }
    const flat=clip(rect,0,-1);
    leaf.style.clipPath=flat.length>2?'polygon('+flat.map(([x,y])=>x+'px '+y+'px').join(',')+')':'inset(100%)';
    ctx.clearRect(0,0,w,h);
    const end=Math.max(...rect.map(dist));
    for(let i=0;i<40 && i*bend/40<end;i++){
      const th=(i+.5)*Math.PI/40, v=Math.round(221+24*Math.sin(th*1.7)-15*Math.sin(th*.8));
      band(i*bend/40,(i+1)*bend/40,'rgb('+v+','+(v-3)+','+(v-12)+')',false);
    }
    if(end>bend)band(bend,end+.01,'#e7e5d9',true);
    if(f<1)frame=requestAnimationFrame(draw);else cleanup();
  }
  leaf.style.zIndex='4'; under.style.zIndex='3';
  leaf.style.visibility=under.style.visibility='visible';
  draw(start);
  return cleanup;
}

export function shufflePages(nodes, before, after) {
  const n=nodes.length, forward=after>before;
  const rank=(i,page)=>(i-page+n)%n;
  const pose=depth=>depth===0?'none':'translate('+Math.min(depth,2)*5+'px,'+Math.min(depth,2)*3+'px) rotate('+Math.min(depth,2)*.6+'deg) scale(.985)';
  const moving=forward?before:after;
  const animations=nodes.flatMap((el,i)=>{
    if(!el || (rank(i,before)>2 && rank(i,after)>2))return [];
    const start={transform:pose(rank(i,before)),zIndex:4-Math.min(rank(i,before),3)};
    const finish={transform:pose(rank(i,after)),zIndex:4-Math.min(rank(i,after),3)};
    const lifted='translate(-10%,-8px) rotate(-4deg) scale(.99)';
    const middle=i===moving?[{offset:.48,transform:lifted,zIndex:forward?6:0},{offset:.49,transform:lifted,zIndex:forward?0:6}]:[];
    return [el.animate([start,...middle,finish],{duration:500,easing:'ease-in-out'})];
  });
  return ()=>animations.forEach(a=>a.cancel());
}
