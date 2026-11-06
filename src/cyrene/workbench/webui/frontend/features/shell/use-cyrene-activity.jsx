import { workbenchServices } from '../../shared/runtime/services.jsx'
import { observeCyreneActivity } from './cyrene-activity.mjs'

export function useCyreneActivity(sessionId) {
  const { Model: model, Runtimes: engine } = workbenchServices.chat();
  const [snapshot, setSnapshot] = React.useState({ sessionId, state: 'idle' });
  React.useEffect(() => observeCyreneActivity({ sessionId, engine, model,
    onState: state => setSnapshot({ sessionId, state }),
  }), [sessionId, engine, model]);
  return snapshot.sessionId === sessionId ? snapshot.state : 'idle';
}
