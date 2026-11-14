import { menuApi } from './assistant-menu-panels.jsx';
import { assistantDeviceActions, runAssistantDeviceAction, deviceMenuPosition } from './assistant-device-actions.mjs';

function ActionIcon({ name }) {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'select' ? <><rect x="3" y="3" width="18" height="14" rx="2"/><path d="M8 21h8m-4-4v4m-5-11 3 3 6-6"/></> : name === 'revoke' ? <><path d="M12 3 4 6v5c0 5 8 10 8 10s8-5 8-10V6Z"/><path d="m9 9 6 6m0-6-6 6"/></> : <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/></>}
  </svg>;
}

export function AssistantDeviceList({ remote, localDeviceName, sessionId, onUpdated, panelRef, portalRef, Icon }) {
  const [menu, setMenu] = React.useState(null), [position, setPosition] = React.useState(null);
  const [busy, setBusy] = React.useState(false), [error, setError] = React.useState(''), [chosen, setChosen] = React.useState(remote?.work_device?.device_id || 'local');
  const popup = React.useRef(null), pending = React.useRef(false);
  React.useEffect(() => { setChosen(remote?.work_device?.device_id || 'local'); }, [remote?.work_device?.device_id]);
  function dismiss(focus = false) { if (focus) menu?.trigger.focus(); setMenu(null); }
  React.useLayoutEffect(() => {
    if (!menu || !popup.current || !panelRef.current) return;
    const surface = popup.current;
    function place() {
      const trigger = menu.trigger.getBoundingClientRect(), bounds = panelRef.current.getBoundingClientRect();
      const scroll = panelRef.current.querySelector('.aim-content')?.getBoundingClientRect() || bounds;
      if (trigger.bottom <= scroll.top || trigger.top >= scroll.bottom) { setMenu(null); return; }
      setPosition(deviceMenuPosition(trigger, bounds, 168, surface.scrollHeight + 2));
    }
    place();
    const observer = new ResizeObserver(place); observer.observe(panelRef.current); observer.observe(surface);
    window.addEventListener('resize', place); window.addEventListener('scroll', place, true);
    return () => { observer.disconnect(); window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
  }, [menu]);
  React.useEffect(() => {
    if (menu && position) popup.current?.querySelector('button:not(:disabled)')?.focus();
  }, [menu, !!position]);
  React.useEffect(() => {
    if (!menu) return;
    function outside(event) {
      if (!popup.current?.contains(event.target) && !menu.trigger.contains(event.target)) dismiss();
    }
    function keyboard(event) {
      if (event.key === 'Escape' || event.key === 'Tab') {
        event.preventDefault(); event.stopImmediatePropagation(); dismiss(true); return;
      }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); event.stopImmediatePropagation();
      const items = Array.from(popup.current?.querySelectorAll('button:not(:disabled)') || []);
      if (!items.length) return;
      let index = items.indexOf(document.activeElement);
      index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
      items[index].focus();
    }
    document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', keyboard, true);
    return () => { document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', keyboard, true); };
  }, [menu]);
  async function act(action, selectedDevice) {
    const device = selectedDevice || menu?.device;
    if (pending.current || !device) return;
    pending.current = true; setBusy(true); setError('');
    try {
      await runAssistantDeviceAction(action, device, { remote, sessionId, api: menuApi,
        notify: (name, detail) => window.dispatchEvent(new CustomEvent(name, { detail })) });
      if (action === 'select') setChosen(device.device_id);
      dismiss(true);
      if (action !== 'select') await onUpdated();
    } catch (e) { setError(e.message); } finally { pending.current = false; setBusy(false); }
  }
  const permissions = menu ? assistantDeviceActions(menu.device, remote) : {};
  return <><section><h4>设备</h4>{[{ device_id: 'local', display_name: remote?.device_name || localDeviceName || '当前设备' }, ...(remote?.peers || [])].map(device => {
      const local = device.device_id === 'local', name = device.display_name || device.device_id;
      const state = local ? 'online' : device.task_state || (device.task_eligible ? 'online' : 'offline');
      return <div className="aim-device aim-device-row" key={device.device_id}>
        <button type="button" className={'aim-device-choice is-' + state} aria-pressed={chosen === device.device_id}
          disabled={busy || !assistantDeviceActions(device, remote || {}).select}
          title={device.task_error || undefined} onClick={() => act('select', device)}>
          <Icon name="device"/><span><span className="aim-item-title">{name}</span><small>{local ? '当前设备' : device.task_error || '在线'}{chosen === device.device_id ? ' · 已选择' : ''}</small></span>
          <i className={'aim-device-light is-' + state} role="img" aria-label={state === 'offline' ? '离线' : state === 'checking' ? '正在检查连接' : '在线'} />
        </button>
        <button type="button" className="aim-device-more" aria-label={name + '的更多操作'} aria-haspopup="menu" aria-expanded={menu?.device.device_id === device.device_id} disabled={busy} onClick={event => {
          setPosition(null); setError(''); setMenu(menu?.device.device_id === device.device_id ? null : { device, trigger: event.currentTarget });
        }}><Icon name="more"/></button>
      </div>;
    })}</section>
    {chosen !== 'local' && remote?.work_device && !(remote.peers || []).some(device => device.device_id === chosen) && <p className="aim-error" role="alert">所选工作设备不可用，请重新选择。</p>}
    {error && <p className="aim-error" role="alert">{error}</p>}
    {menu && portalRef.current && ReactDOM.createPortal(<div ref={popup} className="wb-card-menu aim-device-menu" role="menu" aria-label={(menu.device.display_name || menu.device.device_id) + '设备操作'} style={{ ...position, visibility: position ? 'visible' : 'hidden' }}>
      <button type="button" role="menuitem" className="wbc-chat-menu-action" disabled={busy || !permissions.select} title={!permissions.select ? menu.device.task_error : undefined} onClick={() => act('select')}><ActionIcon name="select"/><span>选择工作设备</span></button>
      <button type="button" role="menuitem" className="wbc-chat-menu-action" disabled={busy || !permissions.revoke} onClick={() => act('revoke')}><ActionIcon name="revoke"/><span>撤销访问权限</span></button>
      <button type="button" role="menuitem" className="wbc-chat-menu-action danger" disabled={busy || !permissions.remove} title={!permissions.remove ? '当前设备不能删除' : undefined} onClick={() => act('remove')}><ActionIcon name="remove"/><span>删除</span></button>
    </div>, portalRef.current)}
  </>;
}
