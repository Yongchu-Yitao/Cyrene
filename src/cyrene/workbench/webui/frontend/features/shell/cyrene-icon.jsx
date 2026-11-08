import { attachCyreneMotion } from './cyrene-motion-dom.mjs';

export function CyreneIcon({ state = "idle" }) {
  const gradientId = "cyrene-brand-" + React.useId().replace(/:/g, "");
  const svgRef = React.useRef(null);
  const controllerRef = React.useRef(null);
  React.useEffect(() => {
    controllerRef.current = attachCyreneMotion(svgRef.current);
    return () => { controllerRef.current.destroy(); controllerRef.current = null; };
  }, []);
  React.useEffect(() => { controllerRef.current?.setState(state); }, [state]);
  return <span className="cyrene-motion"><svg ref={svgRef} className="cyrene-icon-logo" viewBox="0 0 32 32" fill="none" aria-hidden="true" data-state={state}>
      <defs><linearGradient id={gradientId} x1="4" y1="27" x2="25" y2="6" gradientUnits="userSpaceOnUse"><stop stopColor="#0879ff"/><stop offset=".5" stopColor="#00b9ec"/><stop offset="1" stopColor="#00dfaf"/></linearGradient></defs>
      <g transform="translate(1.25 0)"><g className="cyrene-icon-body">
        <g className="cyrene-icon-ring">
        <path d="M23.6 8.4a10.75 10.75 0 1 0 0 15.2" stroke={"url(#" + gradientId + ")"} strokeWidth="4.8" strokeLinecap="round"/>
        <path className="cyrene-icon-glint" d="M23.6 8.4a10.75 10.75 0 1 0 0 15.2" pathLength="100" stroke="#c1fff0" strokeWidth="2.8" strokeLinecap="round"/>
        </g>
        <g className="cyrene-icon-gaze" fill="currentColor"><rect className="cyrene-icon-eye" x="12.15" y="13.1" width="2.2" height="4.5" rx="1.1"/><rect className="cyrene-icon-eye cyrene-icon-eye-right" x="17.2" y="13.1" width="2.2" height="4.5" rx="1.1"/></g>
      </g></g>
      <g className="cyrene-icon-badge cyrene-icon-done"><circle cx="26.3" cy="25.6" r="4.2"/><path d="m24.3 25.6 1.3 1.4 2.5-2.7"/></g>
      <g className="cyrene-icon-badge cyrene-icon-error"><circle cx="26.3" cy="25.6" r="4.2"/><path d="M26.3 23.4v2.2m0 1.7v.1"/></g>
      <g className="cyrene-icon-badge cyrene-icon-wait"><circle cx="26.3" cy="25.6" r="4.2"/><path d="M25.1 24.2a1.2 1.2 0 1 1 1.8 1.1q-.6.3-.6 1m0 1.1v.1"/></g>
      <g className="cyrene-icon-badge cyrene-icon-pause"><circle cx="26.3" cy="25.6" r="4.2"/><path d="M25 24v3.2m2.6-3.2v3.2"/></g>
      <g className="cyrene-icon-badge cyrene-icon-reconnect"><circle cx="26.3" cy="25.6" r="4.2"/><path d="M24.3 25.6h.1m1.9 0h.1m1.9 0h.1"/></g>
    </svg></span>;
}
