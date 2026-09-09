// React delegates wheel as passive. These local gesture handlers intentionally
// cancel scrolling, so bind them at the scroll owner, not the React root.
export function useNativeWheel(handler, forwardedRef) {
  const latest = React.useRef(handler);
  latest.current = handler;
  return React.useMemo(() => {
    let previous = null;
    const listener = event => latest.current(event);
    return node => {
      if (previous) previous.removeEventListener('wheel', listener);
      previous = node;
      if (node) node.addEventListener('wheel', listener, { passive: false });
      if (typeof forwardedRef === 'function') forwardedRef(node);
      else if (forwardedRef) forwardedRef.current = node;
    };
  }, [forwardedRef]);
}
