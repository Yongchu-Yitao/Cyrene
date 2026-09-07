(async function () {
  'use strict';
  const bridge = window.cyreneRemoteDesktopIndicator;
  const context = await bridge.context();
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme === 'light' ? 'light' : 'dark';
  }
  applyTheme(context.theme);
  bridge.onThemeChanged(applyTheme);
  const zh = String(context.language || '').toLowerCase().startsWith('zh');
  const controller = String(context.controller_name || '').trim() || (zh ? '已配对设备' : 'Paired device');
  const isRemoteLogin = context.mode === 'remote_login';
  const canControl = context.can_control === true;
  const copy = zh ? {
    title: isRemoteLogin ? '远程会话进行中' : canControl ? '屏幕正在被控制' : '屏幕正在共享',
    detail: isRemoteLogin ? `${controller} · 正在使用系统登录会话` : canControl
      ? `${controller} · 可查看和操作当前桌面`
      : `${controller} · 可查看当前桌面`,
    disconnect: '紧急断开',
    disconnecting: '正在断开',
  } : {
    title: isRemoteLogin ? 'Remote session active' : canControl ? 'Screen under remote control' : 'Screen sharing active',
    detail: isRemoteLogin ? `${controller} · Using a system login session` : canControl
      ? `${controller} · Can view and control this desktop`
      : `${controller} · Can view this desktop`,
    disconnect: 'Disconnect',
    disconnecting: 'Disconnecting',
  };
  document.documentElement.lang = zh ? 'zh-CN' : 'en';
  document.title = copy.title;
  document.getElementById('title').textContent = copy.title;
  const detail = document.getElementById('detail');
  detail.textContent = copy.detail;
  detail.title = copy.detail;
  const button = document.getElementById('disconnect');
  const buttonLabel = document.getElementById('disconnect-label');
  buttonLabel.textContent = copy.disconnect;
  button.setAttribute('aria-label', copy.disconnect);
  button.addEventListener('click', async function () {
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    buttonLabel.textContent = copy.disconnecting;
    try {
      await bridge.disconnect();
    } catch (_) {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      buttonLabel.textContent = copy.disconnect;
    }
  });
  document.documentElement.dataset.ready = 'true';
})();
