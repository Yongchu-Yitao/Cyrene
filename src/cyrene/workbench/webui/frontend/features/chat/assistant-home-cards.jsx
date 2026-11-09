import React, { useEffect, useState } from 'react';
import { requestCard, WbcAssistantCard } from './assistant-cards.jsx';
import { weatherKind, weatherPeriod } from './card-weather-model.mjs';

export function AssistantHomeCards({ active, sessionId, onReady, visible = true }) {
  const [cards, setCards] = useState([]);
  const [displayTime, setDisplayTime] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    const tick = () => setDisplayTime(new Date());
    tick();
    const timer = setInterval(tick, 60000);
    window.addEventListener('focus', tick);
    document.addEventListener('visibilitychange', tick);
    return () => { clearInterval(timer); window.removeEventListener('focus', tick); document.removeEventListener('visibilitychange', tick); };
  }, [active]);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!active) return;
    let cancelled = false, timer;
    const controller = new AbortController();
    async function refresh() {
      if (document.hidden) { timer = setTimeout(refresh, 5000); return; }
      try {
        const result = await requestCard('/api/assistant/cards/home', { signal: controller.signal });
        if (cancelled) return;
        const order = ['overview', 'resume', 'results'];
        const next = result.cards.filter(card => card.status === 'active').sort((a, b) => order.indexOf(a.spec.home_kind) - order.indexOf(b.spec.home_kind));
        setCards(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
        setError('');
      } catch (err) {
        if (!cancelled && err.status !== 404) setError('欢迎卡片暂时无法更新。');
        // A disabled plugin should not generate repeated missing-route requests.
        if (err.status === 404) return;
      }
      if (!cancelled) timer = setTimeout(refresh, 5000);
    }
    refresh();
    return () => { cancelled = true; clearTimeout(timer); controller.abort(); };
  }, [active, sessionId, attempt]);
  useEffect(() => { if (cards.length) onReady?.(); }, [cards.length, onReady]);
  if (!visible || (!cards.length && !error)) return null;
  const weather = cards.find(card => card.spec.home_kind === 'overview')?.spec.blocks.find(block => block.type === 'weather');
  return <section className="wbc-home-cards wbc-assistant-home" data-weather={weather ? weatherKind(weather, displayTime) : undefined} data-period={weatherPeriod(displayTime)} aria-label="助理首页">
    {weather && (<div className="wbc-home-atmosphere" data-period={weatherPeriod(displayTime)} data-weather={weatherKind(weather, displayTime)} aria-hidden="true"><div className="wbc-home-precipitation">{Array.from({length:18}, (_, i) => <b key={i} style={{'--x': ((i * 31 + 7) % 100) + '%', '--y': ((i * 19 + 3) % 66) + '%', '--speed': (1.4 + (i % 5) * .23) + 's', '--delay': (-i * .37) + 's'}} />)}</div></div>)}
    <div className="wbc-home-grid">{cards.map(card => <WbcAssistantCard key={card.card_id} cardId={card.card_id} initialCard={card} displayTime={displayTime} />)}</div>
    {error && <p className="wbc-card-notice" role="status">{error} <button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button></p>}
  </section>;
}
