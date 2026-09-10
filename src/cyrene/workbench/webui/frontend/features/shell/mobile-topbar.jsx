const { useState: useWorkbenchState, useRef: useWorkbenchRef, useEffect: useWorkbenchEffect } = React;

export function useMobileTopbar() {
  var [compactTopbar, setCompactTopbar] = useWorkbenchState(() => window.matchMedia('(max-width: 767px)').matches);
  var [moreOpen, setMoreOpen] = useWorkbenchState(false);
  var moreRef = useWorkbenchRef(null);
  useWorkbenchEffect(function () {
    var query = window.matchMedia('(max-width: 767px)');
    function change() { setCompactTopbar(query.matches); setMoreOpen(false); }
    query.addEventListener('change', change);
    return () => query.removeEventListener('change', change);
  }, []);
  useWorkbenchEffect(function () {
    if (!moreOpen) return;
    function dismiss(event) {
      if (event.type === 'keydown' && event.key !== 'Escape') return;
      if (event.type !== 'keydown' && moreRef.current?.contains(event.target)) return;
      setMoreOpen(false);
      if (event.type === 'keydown') moreRef.current?.querySelector('.workbench-mobile-more')?.focus();
    }
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', dismiss);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', dismiss); };
  }, [moreOpen]);
  var [tabStripWidth, setTabStripWidth] = useWorkbenchState(0);
  var [projectActionAnchor, setProjectActionAnchor] = useWorkbenchState(null);
  return { compactTopbar, moreOpen, setMoreOpen, moreRef, tabStripWidth, setTabStripWidth, projectActionAnchor, setProjectActionAnchor };
}
