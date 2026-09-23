import { useWbcEffect as useEffect, useWbcRef as useRef, useWbcState as useState } from './core.jsx';

// Keep positioning and keyboard behavior independent from graph mutations.
export function GraphContextMenu({ menu, close, label, children }) {
  const ref = useRef(null), previousFocus = useRef(null);
  const [position, setPosition] = useState({ left: menu.x, top: menu.y });
  useEffect(() => {
    const element = ref.current, parent = element.parentElement;
    previousFocus.current = document.activeElement;
    const place = () => setPosition({ left: Math.max(8, Math.min(menu.x, parent.clientWidth - element.offsetWidth - 8)), top: Math.max(8, Math.min(menu.y, parent.clientHeight - element.offsetHeight - 8)) });
    const observer = new ResizeObserver(place); observer.observe(element); observer.observe(parent); place();
    element.focus();
    const outside = e => { if (!element.contains(e.target)) close(); };
    const escape = e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } };
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', escape, true);
    window.addEventListener('blur', close);
    return () => {
      observer.disconnect(); document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('keydown', escape, true); window.removeEventListener('blur', close);
      if (element.contains(document.activeElement)) previousFocus.current?.focus();
    };
  }, [menu]);
  return <div ref={ref} role="menu" aria-label={label} tabIndex={-1} className="wb-item-context-menu wbc-graph-context-menu" style={position}
    onContextMenu={e => { e.preventDefault(); e.stopPropagation(); }}
    onKeyDown={e => navigateMenu(e, close)} onClick={e => { if (e.target.closest('button:not(:disabled)')) close(); }}>
    {children}
  </div>;
}

function navigateMenu(event, close) {
  const { key, currentTarget } = event;
  if (key === 'Tab') { close(); return; }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(key)) return;
  event.preventDefault(); event.stopPropagation();
  const items = [...currentTarget.querySelectorAll('[role="menuitem"]:not(:disabled)')];
  if (!items.length) return;
  const index = items.indexOf(document.activeElement);
  const target = key === 'Home' ? 0 : key === 'End' ? items.length - 1 : nextMenuIndex(index, key, items.length);
  items[target].focus();
}

function nextMenuIndex(index, key, length) {
  if (index < 0) return key === 'ArrowDown' ? 0 : length - 1;
  return (index + (key === 'ArrowDown' ? 1 : -1) + length) % length;
}
