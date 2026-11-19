export function ActionIcon({name}){
  return <span className="settings-action-icon" aria-hidden="true" dangerouslySetInnerHTML={{__html:window.CyreneIconAssets.settings[name]}}/>;
}
