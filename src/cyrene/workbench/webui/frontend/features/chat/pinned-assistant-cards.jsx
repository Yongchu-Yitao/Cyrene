import { ConversationCardGroup } from './conversation-card-group.jsx';
import React, { useEffect, useRef, useState } from 'react';
import { WbcAssistantCard, requestCard } from './assistant-cards.jsx';
import { clearPinnedCards, loadPinnedCards } from './pinned-card-model.mjs';
import { WbcPaneCardFrame } from './split-pane.jsx';

export async function unpinConversationCards(chatId, placement) {
  try {
    await clearPinnedCards(requestCard, chatId, placement, card_id => {
      window.dispatchEvent(new CustomEvent('cyrene:card-updated', { detail: { card_id } }));
    });
  } finally {
    window.dispatchEvent(new CustomEvent('cyrene:card-placement-changed'));
  }
}

export function usePinnedAssistantCards(active, chatId) {
  const [result, setResult] = useState(null);
  useEffect(() => {
    if (!active || !chatId) return;
    const controller = new AbortController();
    let timer, running = false, again = false;
    async function refresh() {
      if (running) { again = true; return; }
      running = true;
      clearTimeout(timer);
      try {
        const next = await loadPinnedCards(requestCard, controller.signal, chatId);
        if (!controller.signal.aborted) {
          setResult(previous => previous?.chatId === chatId && !previous.error && previous.supportsBulkUnpin === next.supportsBulkUnpin && JSON.stringify(previous.cards) === JSON.stringify(next.cards)
            ? previous : { chatId, ...next, error: '' });
        }
      } catch (e) {
        if (!controller.signal.aborted && e.status !== 404) setResult(previous => ({ chatId, cards: previous?.chatId === chatId ? previous.cards : [], error: e.message }));
      } finally {
        running = false;
        if (!controller.signal.aborted) { timer = setTimeout(refresh, again ? 0 : 5000); again = false; }
      }
    }
    refresh();
    window.addEventListener('cyrene:card-placement-changed', refresh);
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener('cyrene:card-placement-changed', refresh); };
  }, [active, chatId]);
  return result?.chatId === chatId && result ? result : { cards: [], error: '' };
}

export function PinnedAssistantCard({ card, chatId }) {
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
      {visible && card.group ? <ConversationCardGroup chatId={chatId} group={card.group} inPinnedColumn /> : visible ? <WbcAssistantCard chatId={chatId} cardId={card.card_id} /> : <div className="wbc-pinned-card-placeholder">{card.title || card.group?.title}</div>}
    </WbcPaneCardFrame>
  </div>;
}
