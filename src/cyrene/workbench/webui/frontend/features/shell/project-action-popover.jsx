import { wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"

// Render outside the project list's scrolling/clipping context. Measure the
// actual menu instead of guessing its direction from the project's row index.
export function ProjectActionPopover({ anchor, theme, onClose, children }) {
  const ref = React.useRef(null);
  React.useLayoutEffect(() => {
    const menu = ref.current;
    if (!menu || !anchor) return;
    const viewport = window.visualViewport;
    const place = () => {
      const x = viewport?.offsetLeft || 0, y = viewport?.offsetTop || 0;
      const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
      const a = anchor.getBoundingClientRect();
      menu.style.maxWidth = Math.max(0, width - 16) + 'px';
      menu.style.maxHeight = Math.max(0, height - 16) + 'px';
      const r = menu.getBoundingClientRect();
      const below = a.bottom + 6;
      const top = below + r.height <= y + height - 8 ? below : a.top - r.height - 6;
      menu.style.left = Math.max(x + 8, Math.min(a.right - r.width, x + width - r.width - 8)) + 'px';
      menu.style.top = Math.max(y + 8, Math.min(top, y + height - r.height - 8)) + 'px';
      menu.style.visibility = 'visible';
    };
    place();
    menu.querySelector('button')?.focus({ preventScroll: true });
    const observer = new ResizeObserver(place);
    observer.observe(menu);
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    viewport?.addEventListener('resize', place);
    viewport?.addEventListener('scroll', place);
    wbSetBrowserOverlayObscured(1);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      viewport?.removeEventListener('resize', place);
      viewport?.removeEventListener('scroll', place);
      wbSetBrowserOverlayObscured(-1);
    };
  }, [anchor]);
  return ReactDOM.createPortal(<div ref={ref} className="workbench-top-project-actions is-floating" role="menu"
    style={{ ...theme, position: 'fixed', bottom: 'auto', right: 'auto', visibility: 'hidden' }}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); anchor?.focus({ preventScroll: true }); }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const buttons = [...ref.current.querySelectorAll('button:not(:disabled)')];
        const i = buttons.indexOf(document.activeElement);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (i + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length;
        buttons[next]?.focus();
      }
    }}>{children}</div>, document.body);
}
