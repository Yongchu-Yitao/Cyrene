import React, { useEffect, useRef, useState } from 'react';

// Preload with the card UI, not on the first click. Keep the controller in its
// own chunk so the main workbench bundle stays within its size budget.
let enterFullscreen;
const controllerReady = import('./card-fullscreen-controller.mjs').then(module => {
  enterFullscreen = module.enterCardFullscreen;
});

export function useCardFullscreen(ref) {
  const [expanded, setExpanded] = useState(false);
  const close = useRef(null), pending = useRef(false);
  useEffect(() => () => { pending.current = false; close.current?.(false); }, []);
  function toggle() {
    if (close.current) { close.current(); return; }
    if (pending.current) return;
    const element = ref.current;
    function enter() {
      if (!element?.isConnected) return;
      close.current = enterFullscreen(element, () => { close.current = null; setExpanded(false); });
      if (close.current) setExpanded(true);
    }
    if (enterFullscreen) { enter(); return; }
    pending.current = true;
    controllerReady.then(() => { if (pending.current) enter(); })
      .finally(() => { pending.current = false; });
  }
  return { expanded, toggle };
}

const Controls = React.lazy(() => import('./card-fullscreen-button.jsx'));
export function CardFullscreenButton(props) {
  return <React.Suspense fallback={null}><Controls {...props} /></React.Suspense>;
}
