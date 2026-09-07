document.documentElement.lang = navigator.language.startsWith('zh') ? 'zh' : 'en';
const zh = navigator.language.startsWith('zh');
const text = (cn, en) => zh ? cn : en;
document.title = text('Cyrene 诊断助手', 'Cyrene Doctor');
document.querySelector('h1').textContent = document.title;
document.getElementById('intro').textContent = text('Cyrene 后端未能启动。这一页面独立于后端，可以检查本地故障。', 'The Cyrene backend could not start. This independent page can inspect local failures.');
const inspect = document.getElementById('inspect');
inspect.textContent = text('检查原因', 'Diagnose');
const retry = document.getElementById('retry');
retry.textContent = text('重试启动', 'Retry startup');
retry.onclick = () => window.cyreneDoctor.retry();
inspect.onclick = async () => {
  inspect.disabled = true;
  const status = document.getElementById('status');
  status.textContent = text('正在检查…', 'Checking…');
  try {
    const result = await window.cyreneDoctor.inspect();
    const root = document.getElementById('findings'); root.replaceChildren();
    status.textContent = result.status === 'completed' ? text('离线检查完成', 'Offline checks completed') : text('Python 诊断不可用。检查运行文件是否存在、应用是否完整；保留配置和数据后重新安装应用。', 'Python diagnosis unavailable. Check the runtime and application installation; preserve configuration and data before reinstalling.');
    for (const item of (result.report && result.report.findings) || []) {
      const article = document.createElement('article');
      const heading = document.createElement('strong'); heading.textContent = '[' + item.status + '] ' + item.summary[zh ? 'zh' : 'en'];
      const body = document.createElement('p'); body.textContent = item.direction[zh ? 'zh' : 'en'];
      const evidence = document.createElement('small'); evidence.textContent = Object.keys(item.evidence || {}).length ? JSON.stringify(item.evidence) : '';
      article.append(heading, body, evidence); root.append(article);
    }
    if (result.reason) status.textContent += '\n' + result.reason;
  } catch (_) { status.textContent = text('诊断无法完成，请检查应用安装和本地启动日志。', 'Diagnosis could not complete; inspect installation and local startup logs.'); }
  finally { inspect.disabled = false; }
};
inspect.click();
