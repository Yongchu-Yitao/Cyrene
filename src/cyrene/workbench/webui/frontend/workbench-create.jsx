import { workbenchServices } from "./shared/runtime/services.jsx"

(function () {
  var useRef = React.useRef;
  var useState = React.useState;

  function T(key, params, fallback) {
    return workbenchServices.i18n().t(key, params, fallback);
  }

  function errorText(error) {
    try {
      var api = workbenchServices.api();
      if (api && typeof api.errorText === "function") return api.errorText(error);
    } catch (ignored) {}
    return String((error && error.message) || error || "");
  }

  var PROJECT_ICONS = ["spark", "briefcase", "rocket", "doc", "people", "code"];
  var PROJECT_COLORS = ["#7c6cf0", "#3b82f6", "#22c08a", "#f5a623", "#ef4d57", "#a855f7"];

  function ProjectIcon({ name }) {
    var paths = {
      spark: <path d="M12 2.5 13.7 9 20 10.7 13.7 12.4 12 19l-1.7-6.6L4 10.7 10.3 9Z" />,
      briefcase: <><rect x="3" y="7.5" width="18" height="12" rx="2" /><path d="M8.5 7.5V6a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v1.5M3 12.5h18" /></>,
      rocket: <><path d="M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2M9 11a9 9 0 0 1 9-9c1.5 0 2 .5 2 2a9 9 0 0 1-9 9M9 11l4 4" /></>,
      doc: <><path d="M6 3.5h7l5 5V20a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 20Z" /><path d="M13 3.5V9h5M9 13h6M9 16.5h6" /></>,
      people: <><circle cx="9" cy="8.5" r="3" /><path d="M3.5 19a5.5 5.5 0 0 1 11 0M16 6.2a3 3 0 0 1 0 5.6M20.5 19a5.5 5.5 0 0 0-3.5-5.1" /></>,
      code: <><path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 6l-3 12" /></>,
    };
    return <svg viewBox="0 0 24 24" width="18" height="18" fill={name === "spark" ? "currentColor" : "none"} stroke={name === "spark" ? "none" : "currentColor"} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{paths[name] || paths.spark}</svg>;
  }

  function WorkbenchNewProjectModal(props) {
    workbenchServices.i18n().use();
    var [step, setStep] = useState(0);
    var [name, setName] = useState("");
    var [description, setDescription] = useState("");
    var [icon, setIcon] = useState("spark");
    var [color, setColor] = useState(PROJECT_COLORS[0]);
    var [workspacePath, setWorkspacePath] = useState(props.defaultWorkspacePath || "");
    var [busy, setBusy] = useState(false);
    var [error, setError] = useState("");
    var colorRef = useRef(null);
    var trimmedName = name.trim();

    function closeOnScrim(event) {
      if (event.target === event.currentTarget && !busy) props.onClose();
    }

    async function chooseWorkspace() {
      setError("");
      try {
        if (window.cyrene && typeof window.cyrene.pickDirectory === "function") {
          var nativeResult = await window.cyrene.pickDirectory();
          if (nativeResult && nativeResult.path) setWorkspacePath(nativeResult.path);
          else if (nativeResult && nativeResult.error) setError(nativeResult.error);
          return;
        }
        var response = await fetch("/api/context/pick-directory", { method: "POST" });
        var payload = await response.json().catch(function () { return {}; });
        if (!response.ok) throw new Error(payload.error || payload.detail || ("HTTP " + response.status));
        if (payload.path) setWorkspacePath(payload.path);
        else if (payload.error) setError(payload.error);
      } catch (errorValue) {
        setError(errorText(errorValue));
      }
    }

    function createProject() {
      if (!trimmedName || busy) return;
      setBusy(true);
      setError("");
      Promise.resolve(props.onCreate({
        name: trimmedName,
        description: description.trim(),
        icon: icon,
        color: color,
        workspacePath: workspacePath.trim() || undefined,
      })).catch(function (errorValue) {
        setError(errorText(errorValue));
        setBusy(false);
      });
    }

    return <div className="wb-create-scrim" onMouseDown={closeOnScrim}>
      <div className="wb-create-modal wb-create-project" role="dialog" aria-modal="true">
        <div className="wb-create-head">
          <b>{T("create.project.title")}</b>
          <button type="button" className="wb-create-x" disabled={busy} onClick={props.onClose} title={T("common.close")} aria-label={T("common.close")}>×</button>
        </div>
        <div className="wb-create-steps">
          {["create.project.step.basic", "create.project.step.finish"].map(function (key, index) {
            return <React.Fragment key={key}>{index ? <span className="wb-create-step-line" /> : null}<span className={"wb-create-step " + (step === index ? "current" : step > index ? "done" : "idle")}><span className="wb-create-step-dot">{step > index ? "✓" : index + 1}</span><span className="wb-create-step-label">{T(key)}</span></span></React.Fragment>;
          })}
        </div>
        <div className="wb-create-body">
          {step === 0 ? <div className="wb-cp-form">
            <label className="wb-cp-label">{T("create.project.name")} <i className="wb-cp-req">*</i></label>
            <input className="wb-cp-input" value={name} maxLength={50} autoFocus placeholder={T("create.project.namePlaceholder")} onChange={function (event) { setName(event.target.value); }} />
            <label className="wb-cp-label">{T("create.project.description")}</label>
            <textarea className="wb-cp-textarea" value={description} maxLength={200} rows={3} placeholder={T("create.project.descriptionPlaceholder")} onChange={function (event) { setDescription(event.target.value); }} />
            <label className="wb-cp-label">{T("create.project.icon")}</label>
            <div className="wb-cp-icons">{PROJECT_ICONS.map(function (value) { return <button type="button" key={value} className={"wb-cp-icon" + (icon === value ? " active" : "")} onClick={function () { setIcon(value); }}><ProjectIcon name={value} /></button>; })}</div>
            <label className="wb-cp-label">{T("create.project.color")}</label>
            <div className="wb-cp-colors">{PROJECT_COLORS.map(function (value) { return <button type="button" key={value} className={"wb-cp-color" + (color === value ? " active" : "")} style={{ background: value }} onClick={function () { setColor(value); }}>{color === value ? "✓" : null}</button>; })}<button type="button" className="wb-cp-color custom" onClick={function () { colorRef.current && colorRef.current.click(); }}>+<input ref={colorRef} type="color" value={color} onChange={function (event) { setColor(event.target.value); }} /></button></div>
            <label className="wb-cp-label">{T("create.project.workspacePath")}</label>
            <button type="button" className="wb-cp-path-button" disabled={busy} onClick={chooseWorkspace}><span className={"wb-cp-path-text" + (workspacePath.trim() ? "" : " empty")}>{workspacePath.trim() || T("create.project.selectWorkspacePath")}</span><span className="wb-cp-path-action">{T("create.project.choosePath")}</span></button>
          </div> : <div className="wb-cp-finish">
            <div className="wb-cp-finish-icon" style={{ background: color }}><ProjectIcon name={icon} /></div>
            <h3>{trimmedName}</h3>
            {description.trim() ? <p className="wb-cp-finish-desc">{description.trim()}</p> : null}
            <div className="wb-cp-finish-meta"><span><i>{T("create.project.path")}</i>{workspacePath.trim() || T("create.project.defaultWorkspace")}</span></div>
            <p className="wb-cp-finish-hint">{T("create.project.finishHint")}</p>
          </div>}
        </div>
        {error ? <div className="wb-create-error" role="alert">{error}</div> : null}
        <div className="wb-create-foot">
          {step === 0 ? <><button type="button" className="wb-btn ghost" onClick={props.onClose}>{T("common.cancel")}</button><button type="button" className="wb-btn primary" disabled={!trimmedName} onClick={function () { setStep(1); }}>{T("common.next")}</button></> : <><button type="button" className="wb-btn ghost" disabled={busy} onClick={function () { setStep(0); }}>{T("common.previous")}</button><button type="button" className="wb-btn primary" disabled={busy} onClick={createProject}>{busy ? T("common.creating") : T("create.project.create")}</button></>}
        </div>
      </div>
    </div>;
  }

  window.CyreneUI.create = window.CyreneUI.register("create", {
    NewProjectModal: WorkbenchNewProjectModal,
  });
})();
