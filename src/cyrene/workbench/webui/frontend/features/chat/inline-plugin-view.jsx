import { WbcThreadItem } from "./conversation.jsx";
import { PluginView } from "../../platform/plugins.jsx";

export function WbcInlinePluginView({ view }) {
  const storageKey = JSON.stringify(["cyrene.inline-plugin-view", view.projectId, view.packId, view.viewId, view.instanceId]);
  const [state, setState] = React.useState(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) return JSON.parse(saved);
    } catch { /* Storage may be unavailable. Use the durable tool result. */ }
    return view.state;
  });
  const currentState = React.useRef(state);
  const onStateChange = React.useCallback(patch => {
    if (!patch || typeof patch !== "object" || Array.isArray(patch)) return;
    const next = { ...currentState.current, ...patch };
    currentState.current = next;
    setState(next);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* Keep this session interactive. */ }
  }, [storageKey]);

  return <WbcThreadItem className="wbc-inline-plugin-view">
    <PluginView payload={{ ...view, state }} onStateChange={onStateChange} />
  </WbcThreadItem>;
}
