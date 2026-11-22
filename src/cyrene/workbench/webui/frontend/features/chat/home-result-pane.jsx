import React, { useEffect, useState } from 'react';
import { requestCard, WbcAssistantCard } from './assistant-cards.jsx';
import { WbcFileVisual } from './file-resources.jsx';
import { WBC_ICONS } from './icons.jsx';
import { WbcArtifactSplit } from './resource-splits.jsx';
import { assistantTaskFiles } from '../shell/assistant-task-resources.jsx';

export function HomeResultPane({ result, onClose }) {
  const [content, setContent] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setContent(null);
    setError('');
    const controller = new AbortController();
    const load = url => requestCard(url, { signal: controller.signal });
    async function files(owner) {
      const response = await load('/api/workbench/chats/' + encodeURIComponent(owner) + '/resources');
      return { candidates: assistantTaskFiles(response.chat, { preservePaths: true }), workspace: response.chat.workspaceOverride };
    }
    async function resolve() {
      const { resolveHomeResult } = await import('./home-result.mjs');
      return resolveHomeResult(result, load, files);
    }
    resolve().then(value => {
      if (!controller.signal.aborted) setContent(value);
    }).catch(err => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, [result]);
  if (content?.file) return <WbcArtifactSplit file={content.file} items={[{ file: content.file, role: 'assistant' }]} label={result.label} onClose={onClose} />;
  const path = result.action.result_target?.file_path;
  const file = { name: path?.split(/[/\\]/).pop() || result.label, path };
  if (error) return <section className="wbc-home-result-pane wbc-result-unavailable" aria-label={result.label}>
    <header className="wbc-result-bar">
      <WbcFileVisual file={file} />
      <span className="wbc-result-name" title={result.label}>{result.label}</span>
      <button type="button" onClick={onClose} aria-label="关闭预览" title="关闭预览">{WBC_ICONS.x}</button>
    </header>
    <div className="wbc-result-empty">
      <WbcFileVisual file={file} />
      <h3>暂时无法打开</h3>
      <p role="alert">{error}</p>
    </div>
  </section>;
  return <section className="wbc-home-result-pane" aria-label={result.label}>
    {content ? <WbcAssistantCard chatId={content.owner} cardId={content.cardId} /> :
      <div className="wbc-result-status"><h3>{result.label}</h3><p role="status">正在打开成果…</p></div>}
  </section>;
}
