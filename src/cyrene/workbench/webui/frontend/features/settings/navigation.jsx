import { WbcRailToolbar } from "../chat/rail-toolbar.jsx"
import { Disclosure } from "./disclosure.jsx"
import { ActionIcon } from "./action-icon.jsx"
import { workbenchServices, settingsFetch, readSettingsResponse } from "./shared.jsx"

function useProviderSearch(active) {
  const [entries, setEntries] = React.useState([])
  React.useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    settingsFetch("/api/settings/model-config", { signal: controller.signal })
      .then(readSettingsResponse).then(payload => {
        const config = workbenchServices.modelSettings().normalizeConfig(payload)
        setEntries(config.connections.map(connection => ({
          tab: "models", connection: connection.id, title: connection.name || connection.id,
          terms: [connection.id, connection.adapter, connection.base_url,
            ...config.profiles.filter(p => p.connection_id === connection.id).map(p => p.name + " " + p.model)],
        })))
      }).catch(() => { if (!controller.signal.aborted) setEntries([]) })
    return () => controller.abort()
  }, [active])
  return entries
}

export function SettingsNavigation({tab, t, groups, tabs, available, navigate, collapsed, collapseControl}) {
  const [query, setQuery] = React.useState("")
  const currentGroup = groups.find(group => group.ids.includes(tab))?.labelKey
  const [expanded, setExpanded] = React.useState(currentGroup)
  React.useEffect(() => { setExpanded(currentGroup) }, [currentGroup, tab])
  const term = query.trim().toLowerCase()
  const providers = useProviderSearch(!!term && available("models"))
  const index = workbenchServices.settingsIndex()
  function go(id, anchor, connection) {
    setQuery("")
    navigate(id, anchor, connection)
  }
  const results = term ? [
    ...Object.values(tabs).map(item => ({
      tab: item.id, title: t(item.labelKey), terms: [item.id, groups.find(g => g.ids.includes(item.id))?.labelKey].filter(Boolean).map(key => key.startsWith("settings.") ? t(key) : key),
    })),
    ...index.items.map(item => ({
      tab: item.tab, anchor: item.id, title: t(item.labelKey), terms: [...item.keywords || [], item.hintKey && t(item.hintKey)],
    })),
    ...providers,
  ].filter(item => available(item.tab) && [item.title, ...item.terms].join(" ").toLowerCase().includes(term)) : []
  function tabButton(id) {
    const item = tabs[id]
    return <button key={id} type="button" className={"settings-overlay-tab" + (id === tab ? " active" : "")}
      aria-current={id === tab ? "page" : undefined} title={t(item.labelKey)} onClick={() => go(id)}>
      {collapsed && <span className="settings-overlay-tab-icon"><ActionIcon name={item.icon}/></span>}
      <span>{t(item.labelKey)}</span>
    </button>
  }
  function handleSearchKey(event) {
    if (event.key === "Escape") setQuery("")
    if (event.key === "Enter" && results.length === 1) {
      const item = results[0]
      go(item.tab, item.anchor, item.connection)
    }
  }
  return <>
    <WbcRailToolbar collapsed={collapsed} collapseControl={collapseControl}
      query={query} onQueryChange={setQuery} onSearchKeyDown={handleSearchKey}
      searchPlaceholder={t("settings.searchAll")} searchLabel={t("settings.searchAll")}/>
    <div className="settings-overlay-nav-scroll">
      {collapsed ? Object.values(tabs).filter(item => !item.hidden && available(item.id)).map(item => tabButton(item.id)) : term ? <>{results.length === 0 && <small role="status">{t("settings.noSettingsResults")}</small>}
        {results.map(item => <button className="settings-overlay-tab settings-search-result" key={[item.tab,item.anchor,item.connection].join("/")}
          onClick={() => go(item.tab,item.anchor,item.connection)}><span>{item.title}</span>
          {(item.anchor || item.connection) && <small>{t(tabs[item.tab].labelKey)}</small>}</button>)}</> :
        <><div className="settings-profile-link">
          <button type="button" className="group-trigger" aria-current={tab === "profile" ? "page" : undefined}
            onClick={() => go("profile")}><ActionIcon name={tabs.profile.icon}/><span>{t(tabs.profile.labelKey)}</span></button>
        </div>
          {groups.map(group => {
            const ids = group.ids.filter(id => !tabs[id].hidden && available(id))
            if (!ids.length) return null
            const open = expanded === group.labelKey, id = "settings-nav-" + group.icon
            return <section key={id}><button className="group-trigger" type="button" aria-expanded={open}
              aria-controls={id} onClick={() => setExpanded(open ? null : group.labelKey)}>
              <ActionIcon name={group.icon}/><span>{t(group.labelKey)}</span><ActionIcon name="chevron-right"/>
            </button><Disclosure id={id} open={open}><div className="nav-children">{ids.map(tabButton)}</div></Disclosure></section>
          })}</>}
    </div>
  </>
}
