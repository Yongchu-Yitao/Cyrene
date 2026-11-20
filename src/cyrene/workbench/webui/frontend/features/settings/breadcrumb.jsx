import { ActionIcon } from "./action-icon.jsx"

export function SettingsBreadcrumb({ parent, current, onBack, direction }) {
  return <div className="settings-crumb" data-slide-direction={direction}>
    {onBack ? <button type="button" onClick={onBack}>{parent}</button> : <span>{parent}</span>}
    <ActionIcon name="chevron-right"/>
    <h1 aria-current="page">{current}</h1>
  </div>;
}
