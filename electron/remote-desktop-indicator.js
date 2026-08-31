(async function () {
  'use strict';
  const bridge = window.cyreneRemoteDesktopIndicator;
  const context = await bridge.context();
  const zh = String(context.language || '').toLowerCase().startsWith('zh');
  const controller = String(context.controller_name || '').trim() || (zh ? '已配对设备' : 'Paired device');
  document.documentElement.lang = zh ? 'zh-CN' : 'en';
  document.getElementById('title').textContent = zh ? '远程桌面已连接' : 'Remote Desktop connected';
  document.getElementById('detail').textContent = zh
    ? `${controller} 正在${context.mode === 'remote_login' ? '使用系统登录会话' : context.can_control ? '查看并控制当前桌面' : '查看当前桌面'}`
    : `${controller} is ${context.mode === 'remote_login' ? 'using a system login session' : context.can_control ? 'viewing and controlling this desktop' : 'viewing this desktop'}`;
  const button = document.getElementById('disconnect');
  button.textContent = zh ? '紧急断开' : 'Disconnect';
  button.addEventListener('click', async function () {
    button.disabled = true;
    await bridge.disconnect().catch(function () {});
  });
})();
