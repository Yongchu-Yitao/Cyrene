const dbus = require('dbus-next');

const ATSPI = 'org.a11y.atspi';
const REGISTRY_BUS = `${ATSPI}.Registry`;
const ROOT_PATH = '/org/a11y/atspi/accessible/root';

function unwrap(value) {
  if (value && typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, 'value')) {
    return unwrap(value.value);
  }
  if (Array.isArray(value)) return value.map(unwrap);
  return value;
}

function encodeRef(busName, objectPath) {
  return Buffer.from(JSON.stringify([String(busName), String(objectPath)]), 'utf8').toString('base64url');
}

function decodeRef(value) {
  try {
    const result = JSON.parse(Buffer.from(String(value || ''), 'base64url').toString('utf8'));
    if (Array.isArray(result) && result.length === 2 && result.every((item) => typeof item === 'string')) {
      return { busName: result[0], objectPath: result[1] };
    }
  } catch (_) {}
  throw new Error('Invalid AT-SPI object reference.');
}

function asRef(value) {
  const pair = unwrap(value);
  if (!Array.isArray(pair) || pair.length < 2) return null;
  const busName = String(pair[0] || '');
  const objectPath = String(pair[1] || '');
  if (!busName || !objectPath || objectPath === '/') return null;
  return { busName, objectPath };
}

function normalizeAction(name) {
  const value = String(name || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (/double.*(click|press|activate)/.test(value)) return 'double_click';
  if (/toggle|check|uncheck|expand|collapse/.test(value)) return 'toggle';
  if (/select|choose/.test(value)) return 'select';
  if (/scroll|page_(up|down|left|right)/.test(value)) return 'scroll';
  if (/drag|move|reorder|resize/.test(value)) return 'drag';
  if (/click|press|activate|open|invoke|launch|jump/.test(value)) return 'press';
  return value || 'invoke';
}

async function maybeCall(iface, method, ...args) {
  if (!iface || typeof iface[method] !== 'function') return undefined;
  try { return unwrap(await iface[method](...args)); } catch (_) { return undefined; }
}

class LinuxAtspiProvider {
  constructor({ dbusModule = dbus, timeoutMs = 5000 } = {}) {
    this.dbus = dbusModule;
    this.timeoutMs = timeoutMs;
    this.sessionBus = null;
    this.accessibilityBus = null;
    this.proxyCache = new Map();
  }

  _timeout(promise, label, timeoutMs = this.timeoutMs) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs} ms.`)), timeoutMs);
      Promise.resolve(promise).then(
        (value) => { clearTimeout(timer); resolve(value); },
        (error) => { clearTimeout(timer); reject(error); },
      );
    });
  }

  async _bus() {
    if (this.accessibilityBus) return this.accessibilityBus;
    try {
      this.sessionBus = this.dbus.sessionBus();
      const busObject = await this._timeout(
        this.sessionBus.getProxyObject('org.a11y.Bus', '/org/a11y/bus'),
        'AT-SPI bus discovery',
      );
      const address = await this._timeout(
        busObject.getInterface('org.a11y.Bus').GetAddress(),
        'AT-SPI bus address',
      );
      this.accessibilityBus = this.dbus.sessionBus({ busAddress: String(unwrap(address) || '') });
      return this.accessibilityBus;
    } catch (error) {
      throw new Error(`AT-SPI2 is unavailable: ${String(error && error.message ? error.message : error)}`);
    }
  }

  async _proxy(ref) {
    const key = `${ref.busName}:${ref.objectPath}`;
    if (!this.proxyCache.has(key)) {
      const bus = await this._bus();
      this.proxyCache.set(key, bus.getProxyObject(ref.busName, ref.objectPath));
    }
    return this.proxyCache.get(key);
  }

  async _properties(proxy, interfaceName) {
    try {
      const iface = proxy.getInterface('org.freedesktop.DBus.Properties');
      return unwrap(await iface.GetAll(interfaceName)) || {};
    } catch (_) { return {}; }
  }

  async _accessible(ref) {
    const proxy = await this._proxy(ref);
    let accessible;
    try { accessible = proxy.getInterface(`${ATSPI}.Accessible`); } catch (_) { accessible = null; }
    const props = await this._properties(proxy, `${ATSPI}.Accessible`);
    const interfaces = await maybeCall(accessible, 'GetInterfaces') || [];
    const role = await maybeCall(accessible, 'GetRoleName')
      || await maybeCall(accessible, 'GetLocalizedRoleName') || 'unknown';
    let children = await maybeCall(accessible, 'GetChildren');
    if (!Array.isArray(children)) {
      const childCount = Number(props.ChildCount || 0);
      children = [];
      for (let index = 0; index < Math.min(childCount, 1000); index += 1) {
        const child = await maybeCall(accessible, 'GetChildAtIndex', index);
        if (child) children.push(child);
      }
    }
    return {
      proxy,
      props,
      interfaces: Array.isArray(interfaces) ? interfaces.map(String) : [],
      role: String(role || 'unknown'),
      children: children.map(asRef).filter(Boolean),
    };
  }

  async _actions(proxy) {
    let iface;
    try { iface = proxy.getInterface(`${ATSPI}.Action`); } catch (_) { return []; }
    const raw = await maybeCall(iface, 'GetActions');
    if (!Array.isArray(raw)) return [];
    return raw.map((entry, index) => {
      const item = unwrap(entry);
      const name = Array.isArray(item) ? String(item[0] || '') : '';
      return { index, name, kind: normalizeAction(name) };
    }).filter((item) => item.name);
  }

  async _node(ref, parentRef = '') {
    const info = await this._accessible(ref);
    const actions = await this._actions(info.proxy);
    let bounds = null;
    try {
      const component = info.proxy.getInterface(`${ATSPI}.Component`);
      const extents = unwrap(await component.GetExtents(0));
      if (Array.isArray(extents) && extents.length >= 4) {
        bounds = { x: Number(extents[0]), y: Number(extents[1]), width: Number(extents[2]), height: Number(extents[3]) };
      }
    } catch (_) {}
    let value = '';
    try {
      const valueProps = await this._properties(info.proxy, `${ATSPI}.Value`);
      if (valueProps.CurrentValue !== undefined) value = String(valueProps.CurrentValue);
    } catch (_) {}
    const nativeRef = encodeRef(ref.busName, ref.objectPath);
    return {
      raw: {
        nativeRef,
        parentNativeRef: parentRef,
        role: info.role,
        name: String(info.props.Name || ''),
        description: String(info.props.Description || ''),
        automationId: String(info.props.AccessibleId || ''),
        value,
        bounds,
        enabled: true,
        actions: [...new Set(actions.map((item) => item.kind))],
        nativeActions: actions.map((item) => item.name),
        actionDescriptors: actions,
        interfaces: info.interfaces,
      },
      children: info.children,
    };
  }

  async listTargets(exclusions = {}) {
    const root = { busName: REGISTRY_BUS, objectPath: ROOT_PATH };
    let applications;
    try { applications = (await this._accessible(root)).children; } catch (error) {
      throw new Error(`Unable to enumerate AT-SPI applications: ${String(error.message || error)}`);
    }
    const targets = [];
    for (const application of applications) {
      try {
        const appInfo = await this._accessible(application);
        const appName = String(appInfo.props.Name || appInfo.role || application.busName);
        if ((exclusions.excludeAppNames || []).some((item) => String(item).toLowerCase() === appName.toLowerCase())) continue;
        const windows = appInfo.children.length ? appInfo.children : [application];
        for (const windowRef of windows) {
          const windowInfo = windowRef === application ? appInfo : await this._accessible(windowRef);
          targets.push({
            appName,
            applicationId: application.busName,
            pid: 0,
            processStartTime: application.busName,
            windowId: encodeRef(windowRef.busName, windowRef.objectPath),
            windowTitle: String(windowInfo.props.Name || appName),
            foreground: false,
            minimized: false,
            bounds: null,
            platform: 'linux',
            semanticRootRef: encodeRef(windowRef.busName, windowRef.objectPath),
          });
        }
      } catch (_) {}
    }
    return targets;
  }

  async enableAccessibility() {
    await this._bus();
    return { ok: true, enabled: true, supported: true, ready: true, provider: 'at-spi2' };
  }

  async snapshot(target, options = {}) {
    const start = options.nativeRef ? decodeRef(options.nativeRef) : decodeRef(target.semanticRootRef || target.windowId);
    const maxNodes = Math.max(1, Math.min(500, Number(options.maxNodes || 120)));
    const maxDepth = Math.max(1, Math.min(24, Number(options.maxDepth || 12)));
    const maxVisited = Math.max(maxNodes * 4, 200);
    const queue = [{ ref: start, depth: 0, parentRef: '' }];
    const nodes = [];
    const seen = new Set();
    let visited = 0;
    while (queue.length && nodes.length < maxNodes && visited < maxVisited) {
      const current = queue.shift();
      const key = `${current.ref.busName}:${current.ref.objectPath}`;
      if (seen.has(key)) continue;
      seen.add(key);
      visited += 1;
      try {
        const item = await this._node(current.ref, current.parentRef);
        nodes.push(item.raw);
        if (current.depth < maxDepth) {
          for (const child of item.children) {
            queue.push({ ref: child, depth: current.depth + 1, parentRef: item.raw.nativeRef });
          }
        }
      } catch (_) {}
    }
    return { ok: true, nodes, truncated: queue.length > 0, visited, provider: 'at-spi2' };
  }

  async inspect(target, nativeRef, options = {}) {
    return this.snapshot(target, { ...options, nativeRef });
  }

  async _readText(proxy) {
    try {
      const text = proxy.getInterface(`${ATSPI}.Text`);
      const props = await this._properties(proxy, `${ATSPI}.Text`);
      return String(await text.GetText(0, Number(props.CharacterCount || -1)));
    } catch (_) { return ''; }
  }

  async perform(target, capability, nativeRef, parameters = {}) {
    const ref = decodeRef(nativeRef);
    const before = await this._node(ref).catch(() => null);
    const proxy = before ? await this._proxy(ref) : null;
    if (!proxy) throw new Error('The AT-SPI element is no longer available.');
    let performed = false;
    if (['press', 'select', 'toggle', 'semantic_double_click', 'semantic_drag', 'scroll'].includes(capability)) {
      const descriptors = (before.raw.actionDescriptors || []);
      const semanticKind = capability === 'semantic_double_click' ? 'double_click' : capability === 'semantic_drag' ? 'drag' : capability;
      const preferred = semanticKind === 'double_click' ? ['double_click'] : [semanticKind, 'press'];
      const descriptor = descriptors.find((item) => preferred.includes(item.kind));
      if (!descriptor) throw new Error(`The element does not expose a semantic ${capability} action.`);
      const action = proxy.getInterface(`${ATSPI}.Action`);
      performed = Boolean(await action.DoAction(descriptor.index));
    } else if (['set_value', 'type_text'].includes(capability)) {
      const editable = proxy.getInterface(`${ATSPI}.EditableText`);
      const existing = capability === 'type_text' && parameters.replace !== true ? await this._readText(proxy) : '';
      const desired = capability === 'type_text' ? `${existing}${String(parameters.text || '')}` : String(parameters.value || '');
      performed = Boolean(await editable.SetTextContents(desired));
    } else {
      throw new Error(`Unsupported AT-SPI capability: ${capability}.`);
    }
    const after = await this._node(ref).catch(() => null);
    const beforeState = before ? JSON.stringify([before.raw.name, before.raw.value, before.raw.actions]) : '';
    const afterState = after ? JSON.stringify([after.raw.name, after.raw.value, after.raw.actions]) : '';
    const verified = performed && (beforeState !== afterState || ['press', 'select', 'semantic_double_click', 'semantic_drag', 'scroll'].includes(capability));
    return {
      ok: true,
      verified,
      uncertain: !verified,
      summary: `${capability} dispatched through AT-SPI2.`,
      diagnostics: { provider: 'at-spi2', performed },
      verification: after ? { ok: true, nodes: [after.raw] } : null,
    };
  }
}

module.exports = { LinuxAtspiProvider, decodeRef, encodeRef, normalizeAction, unwrap };
