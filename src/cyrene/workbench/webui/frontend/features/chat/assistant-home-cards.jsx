import React, { useEffect, useRef, useState } from 'react';
import { requestCard, useCardAnimations, WbcAssistantCard } from './assistant-cards.jsx';
import { observeHomeCards } from './card-background-work.mjs';
import { homePanelId } from './assistant-home-layout.jsx';

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
  return <section ref={homeElement} className="wbc-home-cards wbc-assistant-home" aria-label="助理首页">
    {cards.length > 0 && <HomeCardList key={sessionId} cards={cards} displayTime={displayTime} />}
    {error && <p className="wbc-card-notice" role="status">{error} <button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button></p>}
  </section>;
}

function HomeCardList({ cards, displayTime }) {
  const disclosure = useState(() => {
    const resume = cards.find(card => card.spec.home_kind === 'resume');
    const timeline = resume?.spec.blocks.find(block => block.type === 'timeline');
    return timeline ? homePanelId(resume, timeline) : null;
  });
  return <div className="wbc-home-grid">{cards.map(card =>
    <WbcAssistantCard key={card.card_id} cardId={card.card_id} initialCard={card}
      displayTime={displayTime} homeDisclosure={disclosure} managedRefresh />
  )}</div>;
}
