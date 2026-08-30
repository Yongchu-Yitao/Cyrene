import { WBC_ICONS } from "../../workbench-chat.jsx"

function WbcResourceListRow({
  ariaLabel,
  buttonTitle,
  className,
  detail,
  detailTitle,
  draggable,
  icon,
  iconAdornment,
  label,
  onClick,
  onDragStart,
}) {
  return (
    <button
      type="button"
      className={"wbc-artifact-list-row" + (className ? " " + className : "")}
      aria-label={ariaLabel}
      draggable={draggable || undefined}
      onClick={onClick}
      onDragStart={onDragStart}
      title={buttonTitle}
    >
      <span className={"wbc-artifact-list-icon" + (iconAdornment ? " wbc-resource-list-icon-adorned" : "")} aria-hidden="true">
        {icon}
        {iconAdornment}
      </span>
      <span className="wbc-artifact-list-copy">
        <b>{label}</b>
        <small title={detailTitle}>{detail}</small>
      </span>
      <span className="wbc-artifact-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
    </button>
  );
}

export { WbcResourceListRow }
