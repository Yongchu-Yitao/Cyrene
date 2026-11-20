import React, { useEffect, useState } from 'react';
import { requestCard, WbcAssistantCard } from './assistant-cards.jsx';
import { WbcArtifactSplit } from './resource-splits.jsx';
import { assistantTaskFiles } from '../shell/assistant-task-resources.jsx';

export function HomeResultPane({ result, onClose }) {
  const [content, setContent] = useState(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const load = url => requestCard(url, { signal: controller.signal });
    async function files(owner) {
      const response = await load('/api/workbench/chats/' + encodeURIComponent(owner) + '/resources');
      return assistantTaskFiles(response.chat);
    }
    async function resolve() {
      const { resolveHomeResult } = await import('./home-result.mjs');
      return resolveHomeResult(result, load, files);
    }
    resolve().then(value => {
      if (!controller.signal.aborted) setContent(value);
    }).catch(err => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, [result, attempt]);
  if (content?.file) return <WbcArtifactSplit file={content.file} items={[{ file: content.file, role: 'assistant' }]} label={result.label} onClose={onClose} />;
  return <section className="wbc-home-result-pane" aria-label={result.label}>
    {content ? <WbcAssistantCard chatId={content.owner} cardId={content.cardId} /> :
      error ? <p role="alert">{error} <button type="button" onClick={() => { setError(''); setAttempt(n => n + 1); }}>重试</button></p> :
        <p role="status">正在打开成果…</p>}
  </section>;
}
