import { CyreneIcon } from './cyrene-icon.jsx';
import { useCyreneActivity } from './use-cyrene-activity.jsx';

// Keep the existing grip as the pointer/keyboard target, including drag and menu.
// The portal is scoped to this conversation's card, even when it moves in a split.
export function AssistantGripLogo({ surfaceRef, sessionId }) {
  const [target, setTarget] = React.useState(null);
  const state = useCyreneActivity(sessionId);
  React.useEffect(() => {
    const card = surfaceRef.current?.closest('.wbc-pane-card');
    setTarget(card?.querySelector('.wbc-side-split-grip-bar-visual') || null);
  }, [surfaceRef, sessionId]);
  return target ? ReactDOM.createPortal(<CyreneIcon state={state} />, target) : null;
}
