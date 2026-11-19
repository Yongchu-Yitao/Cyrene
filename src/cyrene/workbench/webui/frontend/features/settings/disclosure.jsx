export function Disclosure({open,id,children}){
  const [seen,setSeen]=React.useState(open);
  const [settled,setSettled]=React.useState(open);
  React.useLayoutEffect(()=>{
    if(open)setSeen(true);
    setSettled(open && matchMedia('(prefers-reduced-motion: reduce)').matches);
  },[open]);
  return <div id={id} className="motion-disclosure" data-open={open} data-settled={settled} aria-hidden={!open} inert={open?undefined:''}
    onTransitionEnd={e=>{if(e.target===e.currentTarget && e.propertyName==='grid-template-rows')setSettled(open)}}>
    <div className="motion-clip">{(open||seen)&&children}</div>
  </div>;
}
