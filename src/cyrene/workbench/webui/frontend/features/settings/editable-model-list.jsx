import { ActionIcon } from "./action-icon.jsx"
import { Disclosure } from "./disclosure.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"

function ModelListToolbar({v}) {
  const t = workbenchServices.i18n().t;
  const failure = v.discovery?.error && !v.discoveryKeyError;
  const loading = !!v.discovery?.loading;
  const menu = React.useRef(null);
  React.useEffect(() => {
    if (!failure) return;
    function outside(event) {
      if (menu.current && !menu.current.contains(event.target)) menu.current.open = false;
    }
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [failure]);
  function refresh() { v.discoverConnection({notify:true, force:true}); }
  return <div className="model-list-toolbar">
    <h4 id="wb-mcfg-profiles-heading">{t("settings.modelList")} <small>{v.profiles.length}</small></h4>
    <div className="model-list-tools">
      {failure ? <details ref={menu} className="model-refresh-error" onKeyDown={event=>{
        if(event.key==="Escape" && menu.current.open){event.stopPropagation();menu.current.open=false;menu.current.querySelector("summary").focus();}
      }}>
        <summary><ActionIcon name="info-circle"/>{t("settings.modelRefreshFailed")}</summary>
        <div className="model-refresh-popup"><strong>{v.discovery.error}</strong>
          <div><button type="button" onClick={refresh}>{t("settings.refreshModels")}</button>
          {v.discovery.technicalDetail && <button type="button" onClick={()=>v.copyModelTechnicalDetail(v.discovery.technicalDetail, v.props)}>{t("settings.copyTechnicalDetails")}</button>}</div>
        </div>
      </details> : <button type="button" className="model-refresh" aria-label={t("settings.refreshModels")} title={t("settings.refreshModels")} disabled={loading} onClick={refresh}>
        {loading?<span className="wb-spinner small"/>:<ActionIcon name="refresh"/>}
      </button>}
      <button type="button" className="model-add" disabled={!!v.profileDraft} onClick={v.addProfile}><ActionIcon name="plus"/>{t("settings.addModel")}</button>
    </div>
  </div>
}

export default function EditableModelList({children,v,empty}){
  const t = workbenchServices.i18n().t;
  const editors=React.Children.toArray(children);
  const [choice,select]=React.useState(editors[0]?.props.profile.id);
  const [open,setOpen]=React.useState(true);
  const region=React.useId(), buttons=React.useRef(new Map());
  const draft=editors.find(item=>item.props.draft);
  React.useLayoutEffect(()=>{if(draft){select(draft.props.profile.id);setOpen(true)}},[draft?.props.profile.id]);
  const active=editors.find(item=>item.props.profile.id===choice)||editors[0];
  const activeId=active?.props.profile.id;
  function close(){buttons.current.get(activeId)?.focus({preventScroll:true});setOpen(false)}
  return <div className="model-split model-split-motion" data-editing={open && !!active}>
    <div className="model-list-column"><ModelListToolbar v={v}/>{!active && empty}<div className="model-selector" aria-label={t("settings.modelList")}>{editors.map(item=>{
      const model=item.props.profile,selected=activeId===model.id&&open;
      return <button key={model.id} ref={node=>{if(node)buttons.current.set(model.id,node);else buttons.current.delete(model.id)}} aria-controls={region} aria-expanded={selected} aria-pressed={selected} onClick={()=>{select(model.id);setOpen(!selected)}}>
        <ActionIcon name="box"/><span><strong>{model.name||model.model||t("settings.newModel")}</strong><code>{model.model||t("settings.pendingModelId")}</code></span><ActionIcon name="chevron-right"/>
      </button>})}</div></div>
    <Disclosure id={region} open={open}>{active&&React.cloneElement(active,{onClose:close})}</Disclosure>
  </div>
}
