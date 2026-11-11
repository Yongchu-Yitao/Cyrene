import React, { useEffect, useRef, useState } from 'react';
import { requestCard, useCardAnimations, WbcAssistantCard } from './assistant-cards.jsx';
import { observeHomeCards } from './card-background-work.mjs';
import { weatherKind, weatherPeriod } from './card-weather-model.mjs';

export function AssistantHomeCards({ active, sessionId, onReady, visible = true }) {
  const [cards, setCards] = useState([]);
  const [displayTime, setDisplayTime] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    let timer;
    const tick = () => { if (!document.hidden) setDisplayTime(new Date()); };
    const visibility = () => {
      clearInterval(timer);
      if (!document.hidden) { tick(); timer = setInterval(tick, 60000); }
    };
    visibility();
    window.addEventListener('focus', tick);
    document.addEventListener('visibilitychange', visibility);
    return () => { clearInterval(timer); window.removeEventListener('focus', tick); document.removeEventListener('visibilitychange', visibility); };
  }, [active]);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!active) return;
    return observeHomeCards({ request: requestCard, onError: setError, onCards: next => {
      setCards(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    } });
  }, [active, sessionId, attempt]);
  useEffect(() => { if (cards.length) onReady?.(); }, [cards.length, onReady]);
  const homeElement = useRef(null);
  const shown = visible && !!(cards.length || error);
  useCardAnimations(homeElement, shown);
  if (!shown) return null;
  const weather = cards.find(card => card.spec.home_kind === 'overview')?.spec.blocks.find(block => block.type === 'weather');
  return <section ref={homeElement} className="wbc-home-cards wbc-assistant-home" data-weather={weather ? weatherKind(weather, displayTime) : undefined} data-period={weatherPeriod(displayTime)} aria-label="助理首页">
    {weather && (<div className="wbc-home-atmosphere" data-period={weatherPeriod(displayTime)} data-weather={weatherKind(weather, displayTime)} aria-hidden="true"><div className="wbc-home-precipitation">{Array.from({length:18}, (_, i) => <b key={i} style={{'--x': ((i * 31 + 7) % 100) + '%', '--y': ((i * 19 + 3) % 66) + '%', '--speed': (1.4 + (i % 5) * .23) + 's', '--delay': (-i * .37) + 's'}} />)}</div></div>)}
    <div className="wbc-home-grid">{cards.map(card => <WbcAssistantCard key={card.card_id} cardId={card.card_id} initialCard={card} displayTime={displayTime} managedRefresh />)}</div>
    {error && <p className="wbc-card-notice" role="status">{error} <button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button></p>}
  </section>;
}
