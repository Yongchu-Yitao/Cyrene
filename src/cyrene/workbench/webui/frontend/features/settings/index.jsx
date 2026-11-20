export { RemotePanel, RemotePeerCard } from "./remote.jsx"
export { GeneralPanel } from "./general.jsx"
export { SearchPanel } from "./search.jsx"
export { ChannelsPanel } from "./channels.jsx"
const AgentSettings = React.lazy(() => import("./agents.jsx"));
export function AgentsPanel(props) {
  return <React.Suspense fallback={<span className="wb-spinner small" role="status"/>}>
    <AgentSettings {...props}/>
  </React.Suspense>;
}
export { AppearancePanel } from "./appearance.jsx"
const VoiceSettings = React.lazy(() => import("./capabilities.jsx"));
export function CapabilitiesPanel(props) {
  return <React.Suspense fallback={<span className="wb-spinner small" role="status"/>}>
    <VoiceSettings {...props}/>
  </React.Suspense>;
}
export { DataPanel, requestDataPanelStorage } from "./data.jsx"
export { AboutPanel } from "./about.jsx"
export { PluginRegistryPanel } from "./custom-plugins.jsx"
export { HooksPanel } from "./plugin-center-cli-hooks.jsx"
export { ShortcutsPanel } from "./shortcuts.jsx"
export { BudgetPanel } from "./budget.jsx"
export { MediaPanel } from "./media.jsx"
