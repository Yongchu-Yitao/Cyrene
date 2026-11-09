import React, { useEffect, useRef, useState } from 'react';
import { WbcAssistantCard, requestCard } from './assistant-cards.jsx';
import { loadPinnedCards } from './pinned-card-model.mjs';
import { WbcPaneCardFrame } from './split-pane.jsx';

export function usePinnedAssistantCards(active) {
  const [cards, setCards] = useState([]);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let timer, running = false, again = false;
    async function refresh() {
      if (running) { again = true; return; }
      running = true;
      clearTimeout(timer);
      try {
        const next = await loadPinnedCards(requestCard, controller.signal);
        if (!controller.signal.aborted) {
          setCards(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
          setError('');
        }
      } catch (e) {
        if (!controller.signal.aborted && e.status !== 404) setError(e.message);
      } finally {
        running = false;
        if (!controller.signal.aborted) { timer = setTimeout(refresh, again ? 0 : 5000); again = false; }
      }
    }
    refresh();
    window.addEventListener('cyrene:card-placement-changed', refresh);
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener('cyrene:card-placement-changed', refresh); };
  }, [active]);
  return { cards, error };
}

export function PinnedAssistantCard({ card }) {
  const holder = useRef(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: '300px' });
    observer.observe(holder.current);
    return () => observer.disconnect();
  }, []);
  return <div ref={holder} className="wbc-pinned-card-slot">
    <WbcPaneCardFrame card={{ id: 'pinned:' + card.card_id, kind: 'assistant-card' }}>
      {visible ? <WbcAssistantCard cardId={card.card_id} /> : <div className="wbc-pinned-card-placeholder">{card.title}</div>}
    </WbcPaneCardFrame>
  </div>;
}
