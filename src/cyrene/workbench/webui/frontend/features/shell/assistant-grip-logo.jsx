import { AssistantIdentityMenu } from './assistant-identity-menu.jsx';

const AssistantLogoContext = React.createContext(null);

// Keep the icon and its motion controller alive across bootstrap and chat mounts.
export function AssistantLogoProvider({ children }) {
  const [target, setTarget] = React.useState(null);
  const lastTarget = React.useRef(null);
  const emptyRef = React.useRef(null);
  if (target) lastTarget.current = target;
  const previous = lastTarget.current;
  const register = React.useCallback(next => {
    setTarget(next);
    return () => setTarget(current => current?.surfaceRef === next.surfaceRef ? null : current);
  }, []);
  return <AssistantLogoContext.Provider value={register}>
    {children}
    <AssistantIdentityMenu {...(target || previous)} surfaceRef={target?.surfaceRef || emptyRef}
      loading={target ? target.loading : !!previous?.loading}
      hidden={!target && (!previous?.loading || !!previous?.sessionId)} />
  </AssistantLogoContext.Provider>;
}

export function AssistantGripLogo({ shared = false, ...props }) {
  const register = React.useContext(AssistantLogoContext);
  const gripActionsRef = React.useRef(props.gripActions);
  gripActionsRef.current = props.gripActions;
  React.useEffect(() => {
    if (shared && register) return register({ ...props, gripActionsRef });
  }, [shared, register, props.surfaceRef, props.sessionId, props.loading]);
  return shared && register ? null : <AssistantIdentityMenu {...props} />;
}
