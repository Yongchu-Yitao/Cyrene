import { cardMapStyles } from './card-map-style.mjs';
import React, { useEffect, useRef, useState } from 'react';
import { wbcWgs84ToGcj02, wbcMapProvider, wbcTileConfig } from './map-provider.mjs';
import { wbcRenderMarkdown } from './runtime-timeline.jsx';
import { customCardDocument, toggleCardSelection } from './assistant-card-model.mjs';

export function CardMap({ block, values, disabled, onChange }) {
  const styles = cardMapStyles(block);
  const holder = useRef(null);
  const map = useRef(null);
  const layers = useRef(null);
  const [provider, setProvider] = useState(() => {
    try { if (localStorage.getItem('cyrene-tweak-map-provider')) return wbcMapProvider(); } catch (_) { /* Use geographic default. */ }
    return block.places.length && block.places.every(p => p.longitude >= 72.004 && p.longitude <= 137.8347 && p.latitude >= 0.8293 && p.latitude <= 55.8271) ? 'amap' : wbcMapProvider();
  });
  const [theme, setTheme] = useState(document.documentElement.dataset.theme);
  const [failed, setFailed] = useState(false);
  const [focus, setFocus] = useState(null);
  const switched = useRef(false);
  const selected = block.selection ? values[block.selection.field_id] : null;
  const selectedIds = Array.isArray(selected) ? selected : [selected];
  const convert = place => provider === 'amap' ? wbcWgs84ToGcj02(place.latitude, place.longitude) : [place.latitude, place.longitude];
  function choose(place) {
    setFocus(place.place_id);
    map.current?.setView(convert(place), 13);
    if (block.selection && !disabled) onChange(block.selection.field_id,
      toggleCardSelection(selected, place.place_id, block.selection.mode));
  }
  const chooseRef = useRef(choose);
  chooseRef.current = choose;
  function fit() {
    if (map.current && block.places.length) map.current.fitBounds(block.places.map(convert), { padding: [28, 28], maxZoom: 13 });
  }
  useEffect(() => {
    if (!window.L) { setFailed(true); return; }
    const L = window.L;
    const instance = L.map(holder.current, { scrollWheelZoom: false }).setView([35, 105], 4);
    map.current = instance;
    layers.current = L.layerGroup().addTo(instance);
    const resize = new ResizeObserver(() => instance.invalidateSize({ pan: false }));
    resize.observe(holder.current);
    const appearance = new MutationObserver(() => setTheme(document.documentElement.dataset.theme));
    appearance.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => { resize.disconnect(); appearance.disconnect(); instance.remove(); map.current = null; };
  }, []);
  useEffect(() => {
    if (!map.current || !window.L) return;
    let errors = 0;
    setFailed(false);
    const config = wbcTileConfig(provider);
    const tile = window.L.tileLayer(config.url, { ...config.options, attribution: provider === 'amap' ? '© 高德地图' : '© OpenStreetMap contributors © CARTO' });
    tile.on('tileerror', () => {
      if (++errors < 3) return;
      if (!switched.current) { switched.current = true; setProvider(provider === 'amap' ? 'direct' : 'amap'); }
      else setFailed(true);
    });
    tile.addTo(map.current);
    return () => { tile.remove(); };
  }, [provider, theme]);
  useEffect(() => {
    const L = window.L;
    if (!L || !layers.current) return;
    layers.current.clearLayers();
    const places = new Map(block.places.map(place => [place.place_id, place]));
    block.places.forEach(place => {
      const active = selectedIds.includes(place.place_id) || focus === place.place_id;
      const marker = L.circleMarker(convert(place), { radius: active ? 10 : 7, color: active ? '#111827' : '#ffffff', weight: active ? 3 : 2, fillColor: styles.placeColor(place), fillOpacity: 1 }).addTo(layers.current);
      const popup = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = place.label; popup.appendChild(title);
      if (place.note) { const note = document.createElement('div'); note.innerHTML = wbcRenderMarkdown(place.note); popup.appendChild(note); }
      marker.bindPopup(popup);
      if (focus === place.place_id) marker.openPopup();
      marker.on('click', () => chooseRef.current(place));
    });
    (block.routes || []).forEach(route => {
      const from = places.get(route.from_place_id), to = places.get(route.to_place_id);
      if (!from || !to) return;
      const points = [convert(from), convert(to)];
      L.polyline(points, { color: '#ffffff', weight: focus === route.route_id ? 8 : 6, opacity: 0.9, interactive: false }).addTo(layers.current);
      const line = L.polyline(points, { color: styles.routeColor(route), weight: focus === route.route_id ? 5 : 3, opacity: 1, dashArray: '8 6' }).addTo(layers.current);
      const popup = document.createElement('div');
      popup.textContent = [from.label + ' → ' + to.label, route.transport, route.note].filter(Boolean).join(' · ');
      line.bindPopup(popup);
      if (focus === route.route_id) line.openPopup();
    });
    layers.current.eachLayer(layer => { if (layer instanceof L.CircleMarker) layer.bringToFront(); });
  }, [block, provider, selected, focus]);
  useEffect(fit, [JSON.stringify(block.places.map(p => [p.latitude, p.longitude])), provider]);
  return <div className="wbc-card-map">
    <div className="wbc-card-map-canvas" ref={holder} aria-label={block.title || '地点地图'} />
    <div className="wbc-card-map-toolbar"><button type="button" onClick={fit}>显示全部地点</button><label>底图 <select aria-label="地图底图" value={provider} onChange={e => { switched.current = false; setProvider(e.target.value); }}><option value="amap">高德</option><option value="direct">CARTO</option></select></label><span>连线表示地点关系，不是导航路线</span></div>
    {styles.groups.length > 0 && <div className="wbc-card-map-legend" aria-label="地图分组图例">{styles.groups.map(group => <span key={group.group_id}><i style={{ background: group.color }} aria-hidden="true" />{group.label}</span>)}{block.places.some(place => !place.group_id) && <span><i style={{ background: styles.placeColor({}) }} aria-hidden="true" />未分组地点</span>}</div>}
    {failed && <p role="status">底图暂时无法加载，仍可使用下方地点列表。</p>}
    {!block.places.length && <p>暂无地点</p>}
    <div className="wbc-card-places">{block.places.map(place => <button type="button" key={place.place_id} onClick={() => choose(place)} aria-pressed={selectedIds.includes(place.place_id)}>
      <strong>{place.label}</strong>{place.note && <span>{place.note}</span>}
    </button>)}</div>
    {(block.routes || []).map(route => <button className="wbc-card-route" key={route.route_id} type="button" onClick={() => {
      setFocus(route.route_id);
      const endpoints = block.places.filter(p => p.place_id === route.from_place_id || p.place_id === route.to_place_id);
      if (map.current && endpoints.length) map.current.fitBounds(endpoints.map(convert), { padding: [40, 40], maxZoom: 13 });
    }}>{block.places.find(p => p.place_id === route.from_place_id)?.label} → {block.places.find(p => p.place_id === route.to_place_id)?.label}{route.transport && ' · ' + route.transport}{route.note && <small>{route.note}</small>}</button>)}
  </div>;
}

export function CardChart({ block }) {
  const holder = useRef(null);
  useEffect(() => {
    if (!window.echarts) return;
    const chart = window.echarts.init(holder.current);
    function render() {
      const color = getComputedStyle(holder.current).color;
      const series = block.series.map(series => block.chart_type === 'pie'
        ? { name: series.label, type: 'pie', radius: ['0%', '65%'], data: block.labels.map((name, i) => ({ name, value: series.values[i] })) }
        : { name: series.label, type: block.chart_type, data: series.values });
      chart.setOption({ animation: false, color: ['#c28b80', '#60a8ab', '#8a91b6'], textStyle: { color }, tooltip: { trigger: block.chart_type === 'pie' ? 'item' : 'axis' }, legend: { textStyle: { color } },
        ...(block.chart_type === 'pie' ? {} : { grid: { left: 40, right: 20, top: 40, bottom: 35, containLabel: true }, xAxis: { type: 'category', data: block.labels, axisLabel: { color } }, yAxis: { type: 'value', axisLabel: { color } } }), series }, true);
    }
    render();
    const resize = new ResizeObserver(() => chart.resize()); resize.observe(holder.current);
    const theme = new MutationObserver(render); theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => { resize.disconnect(); theme.disconnect(); chart.dispose(); };
  }, [block]);
  return <div><div className="wbc-card-chart" ref={holder} role="img" aria-label={block.title || '数据图表'} /><details><summary>查看图表数据</summary><table><thead><tr><th>项目</th>{block.series.map((s, i) => <th key={i}>{s.label}</th>)}</tr></thead><tbody>{block.labels.map((label, i) => <tr key={i}><th>{label}</th>{block.series.map((s, j) => <td key={j}>{s.values[i]}</td>)}</tr>)}</tbody></table></details></div>;
}

export function CardEmbed({ block, card, values, disabled, onEvent }) {
  const frame = useRef(null);
  const callback = useRef(onEvent); callback.current = onEvent;
  const [proposal, setProposal] = useState(null);
  const doc = React.useMemo(() => customCardDocument(block.html, Object.keys(card.spec.input_schema.properties)), [block.html, JSON.stringify(Object.keys(card.spec.input_schema.properties))]);
  function publishState() {
    frame.current?.contentWindow?.postMessage({ channel: 'cyrene-card-state', card_id: card.card_id, revision: card.revision, values, readonly: disabled }, '*');
  }
  useEffect(() => {
    function receive(event) {
      if (event.source !== frame.current?.contentWindow || event.origin !== 'null' || disabled) return;
      const data = event.data;
      if (data?.channel !== 'cyrene-card' || !['change', 'submit', 'confirm'].includes(data.kind)
        || !data.values || typeof data.values !== 'object' || Array.isArray(data.values)) return;
      if (JSON.stringify(data).length > 65536) return;
      const action = card.spec.actions.find(a => a.action_id === data.action_id && a.kind === data.kind);
      if (data.kind !== 'change' && !action) return;
      if (data.kind === 'change') {
        if (document.activeElement !== frame.current || !navigator.userActivation?.isActive) return;
        callback.current('change', data.values);
      } else {
        // Generated JavaScript cannot manufacture a user confirmation. Final
        // iframe actions require a native click after values are displayed.
        setProposal({ kind: data.kind, values: data.values, action });
      }
    }
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, [card.spec.actions, disabled]);
  useEffect(publishState, [values, disabled, card.revision]);
  useEffect(() => setProposal(null), [card.revision, block.html]);
  return <div className="wbc-card-embed"><iframe ref={frame} title={block.title || card.spec.title} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={doc} style={{ height: 360 }} onLoad={publishState} />{proposal && <div className="wbc-card-embed-proposal"><p>{proposal.action.label}</p><ul>{Object.values(proposal.values).map((value, i) => <li key={i}>{Array.isArray(value) ? value.join('、') : String(value)}</li>)}</ul><button type="button" disabled={disabled} onClick={() => { callback.current(proposal.kind, proposal.values, proposal.action); setProposal(null); }}>确认提交</button><button type="button" onClick={() => setProposal(null)}>取消</button></div>}</div>;
}
