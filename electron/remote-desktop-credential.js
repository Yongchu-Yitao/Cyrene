(function () {
  'use strict';
  const bridge = window.cyreneCredentialDialog;
  const catalogs = {
    en: {
      title: 'Remote Desktop sign in', subtitle: 'Credentials are used once for this encrypted RDP session and are not saved.',
      username: 'Username', domain: 'Domain (optional)', password: 'Password', cancel: 'Cancel', connect: 'Connect',
    },
    zh: {
      title: '远程桌面登录', subtitle: '凭据只用于本次加密 RDP 会话，不会保存。',
      username: '用户名', domain: '域（可选）', password: '密码', cancel: '取消', connect: '连接',
    },
  };
  bridge.context().then(function (context) {
    const language = String(context && context.language || 'en').toLowerCase().startsWith('zh') ? 'zh' : 'en';
    const messages = catalogs[language];
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
    document.title = messages.title;
    document.getElementById('title').textContent = messages.title;
    const device = String(context && context.device_name || '');
    document.getElementById('device').textContent = device ? `${device} · ${messages.subtitle}` : messages.subtitle;
    document.getElementById('username-label').textContent = messages.username;
    document.getElementById('domain-label').textContent = messages.domain;
    document.getElementById('password-label').textContent = messages.password;
    document.getElementById('cancel').textContent = messages.cancel;
    document.getElementById('submit').textContent = messages.connect;
    document.getElementById('username').focus();
  });
  document.getElementById('cancel').addEventListener('click', function () { bridge.cancel(); });
  document.getElementById('form').addEventListener('submit', function (event) {
    event.preventDefault();
    bridge.submit({
      username: document.getElementById('username').value,
      domain: document.getElementById('domain').value,
      password: document.getElementById('password').value,
    });
    document.getElementById('password').value = '';
  });
})();
