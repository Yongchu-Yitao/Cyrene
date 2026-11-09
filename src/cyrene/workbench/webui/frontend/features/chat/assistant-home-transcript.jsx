import React, { useEffect, useRef, useState } from 'react';
import { AssistantHomeCards } from './assistant-home-cards.jsx';
import { requestCard } from './assistant-cards.jsx';
import { wbcProjectTranscript } from './runtime-timeline.jsx';
import { captureHomePosition } from './home-timeline.mjs';

// Only a claimed daily/time-slot display moves the home. Data updates never move it.
export function AssistantHomeTranscript({ Transcript, messages, runtime, sessionId, active, onOpenFile }) {
  const [position, setPosition] = useState(null);
  const [clientId] = useState(() => crypto.randomUUID());
  const shownSlot = useRef(null);
  const [ready, setReady] = useState(false);
  const latest = useRef(null);
  latest.current = { messages, runtime };
  const onReady = useRef(() => setReady(true)).current;
  useEffect(() => {
    if (!active || !ready) return;
    let cancelled = false, pending = false;
    async function check() {
      if (cancelled || pending || document.hidden) return;
      pending = true;
      try {
        const result = await requestCard('/api/assistant/cards/home/display', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: clientId, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone}),
        });
        if (!cancelled && result.slot && result.slot !== shownSlot.current) {
          shownSlot.current = result.slot;
          const snapshot = latest.current;
          setPosition(captureHomePosition(wbcProjectTranscript(snapshot.messages, snapshot.runtime), sessionId));
        }
      } catch { /* Keep the current surface and retry on the next tick. */ }
      finally { pending = false; }
    }
    check();
    const timer = setInterval(check, 30000);
    window.addEventListener('focus', check);
    document.addEventListener('visibilitychange', check);
    return () => {
      cancelled = true;
      clearInterval(timer);
      window.removeEventListener('focus', check);
      document.removeEventListener('visibilitychange', check);
    };
  }, [active, ready, sessionId, clientId]);
  return <Transcript messages={messages} runtime={runtime} chatId={sessionId} onOpenFile={onOpenFile}
    homeInsertion={{ position, content: <AssistantHomeCards active={active} sessionId={sessionId} onReady={onReady} visible={!!position} /> }} />;
}
