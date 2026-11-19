export function ExtensionFilter({ query, onChange, label, count, disabled = false }) {
  return <div className="wb-extension-filter">
    <input className="wb-input" value={query} disabled={disabled} aria-label={label}
      placeholder={label} onChange={event => onChange(event.target.value)}/>
    <span>{count}</span>
  </div>
}
