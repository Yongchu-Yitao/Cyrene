import { ReactFlow, Background, Controls, MiniMap, Handle, Position, useNodesState, useEdgesState } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './context-graph.css';
import { wbcT, useWbcState as useState, useWbcEffect as useEffect, useWbcRef as useRef } from './core.jsx';
import { visibleGraph, graphMarkdown, graphDiff, graphSvg } from './context-graph-model.mjs';
import { WBC_ICONS } from './icons.jsx';
import { GraphContextMenu } from './context-graph-menu.jsx';
const graphText = key => wbcT('workbenchChat.graph.' + key, key);

async function request(url, method = 'GET', data, signal) {
  const response = await fetch(url, { method, signal, headers: { 'Content-Type': 'application/json' }, ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
  const result = await response.json();
  if (!response.ok || result.error) throw new Error(result.detail || result.error?.message || result.error || graphText('error'));
  return result;
}
const base = id => '/api/workbench/chats/' + encodeURIComponent(id) + '/context-graph';
function download(blob, name) {
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function graphNodeClass(data) { return 'wbc-graph-node' + (data.active ? ' is-active' : '') + (data.match ? ' is-match' : '') + (data.historical ? ' is-history' : '') + (data.groupItems ? ' is-context-group' : ''); }
function graphNodeBadge(data) { if (data.draft) return graphText('draft'); if (data.retry) return '↻'; if (data.manual) return graphText('manual'); return data.tip ? '●' : ''; }
function graphNodeTitle(data) {
  if (data.groupItems) return graphText(data.groupKind) + ' · ' + data.groupItems.length;
  if (data.role === 'task') return graphText('taskDocument');
  if (data.role === 'context') return wbcT('workbenchChat.contextKind.' + data.kind, graphContextLabel(data.kind));
  return wbcT('workbenchChat.contextRole.' + data.role, data.role);
}
function ContextNode({ data }) {
  return <div className={graphNodeClass(data)}>
    <Handle type="target" position={Position.Left} isConnectable={false} />
    <div className="wbc-graph-node-head"><b>{graphNodeTitle(data)}</b><span>{graphNodeBadge(data)}</span></div>
    <p>{data.groupItems ? data.groupItems.map(graphNodeTitle).join(' · ') : data.preview || graphText('start')}</p><small>{data.groupItems ? graphText('inspectGroup') : data.pending ? graphText('pending') : data.kind || ''}</small>
    {data.canCollapse && <button type="button" className="nodrag" aria-label={graphText('collapse')} onClick={e => { e.stopPropagation(); data.toggle(data.id, !!data.groupItems); }}>{data.collapsed ? '+' : '−'}</button>}
    <Handle type="source" position={Position.Right} isConnectable={false} />
  </div>;
}
const nodeTypes = { context: ContextNode };

function useGraphDataState() {
  const [graph, setGraph] = useState(null), [view, setView] = useState({ positions: {}, collapsed: [] });
  const [nodes, setNodes, onNodesChange] = useNodesState([]), [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selected, setSelected] = useState(''), [detail, setDetail] = useState(null), [draft, setDraft] = useState('');
  const [inspectionChat, setInspectionChat] = useState('');
  const [query, setQuery] = useState(''), [scope, setScope] = useState('snapshot'), [tab, setTab] = useState('details');
  return { graph, setGraph, view, setView, nodes, setNodes, onNodesChange, edges, setEdges, onEdgesChange, selected, setSelected, detail, setDetail, draft, setDraft, inspectionChat, setInspectionChat, query, setQuery, scope, setScope, tab, setTab };
}

function useGraphEditorState() {
  const [editing, setEditing] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [copySource, setCopySource] = useState(null), [copyTarget, setCopyTarget] = useState(null);
  const [pinned, setPinned] = useState(null), [notice, setNotice] = useState(''), [notes, setNotes] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false), [pendingSelection, setPendingSelection] = useState(null);
  const [exportType, setExportType] = useState('json'), [newContext, setNewContext] = useState(false);
  return { editing, setEditing, busy, setBusy, error, setError, copySource, setCopySource, copyTarget, setCopyTarget, pinned, setPinned, notice, setNotice, notes, setNotes, confirmDelete, setConfirmDelete, pendingSelection, setPendingSelection, exportType, setExportType, newContext, setNewContext };
}

function useGraphRequestRefs(view) {
  const layoutCache = useRef(new Map());
  const canvasRef = useRef(null);
  const flow = useRef(null), layoutRevision = useRef(0), loadedView = useRef(''), graphRequest = useRef(0);
  const viewRevision = useRef(0), history = useRef([]), future = useRef([]), dirtyView = useRef(false), operation = useRef(null);
  const currentView = useRef(view); currentView.current = view;
  return { layoutCache, canvasRef, flow, layoutRevision, loadedView, graphRequest, viewRevision, history, future, dirtyView, operation, currentView };
}

function graphSelection(state) {
  const { graph, nodes, selected, detail, draft, editing, inspectionChat, notes, newContext, chatId } = state;
  const contentDirty = editing && draft !== (newContext ? '' : detail?.content || '');
  const displayNode = nodes.find(n => n.id === selected)?.data || graph?.nodes.find(n => n.id === selected);
  const node = displayNode?.members?.find(n => n.chatId === inspectionChat) || displayNode;
  const branch = graph?.branches.find(b => b.chatId === node?.chatId);

  const dirty = contentDirty || (!!branch && notes !== (branch.value?.notes || ''));


  return { contentDirty, displayNode, node, branch, dirty };
}

function useGraphRead(state) {
  const { setScope, setGraph, view, setView, nodes, selected, setSelected, detail, setDetail, setDraft, editing, setEditing, setError, inspectionChat, setInspectionChat, notes, setNotes, setConfirmDelete, setNewContext, layoutRevision, loadedView, graphRequest, viewRevision, dirtyView, chatId, onDirtyChange, node, branch, dirty } = state;
  useEffect(() => { onDirtyChange?.(dirty); return () => onDirtyChange?.(false); }, [dirty]);
  async function load() {
    const rev = ++graphRequest.current;
    try {
      const result = await request(base(chatId));
      if (rev !== graphRequest.current) return;
      setGraph(previous => JSON.stringify(previous) === JSON.stringify(result) ? previous : result); setError('');
      if (loadedView.current !== result.familyId) {
        loadedView.current = result.familyId; viewRevision.current = result.view.revision;
        setView({ positions: {}, collapsed: [], ...(result.view.value.layoutVersion === 2 ? result.view.value : {}) }); setSelected(result.view.value.selected || ''); setInspectionChat(result.view.value.inspectionChat || ''); dirtyView.current = false;
      }
      return result;
    } catch (e) { if (rev === graphRequest.current) setError(e.message); }
  }
  useEffect(() => {
    load(); const timer = setInterval(load, 5000);
    return () => { clearInterval(timer); graphRequest.current++; layoutRevision.current++; };
  }, [chatId]);
  useEffect(() => {
    if (!node?.nodeId) { setDetail(null); return; }
    const abort = new AbortController(); setDetail(null); setEditing(false); setNewContext(false); setScope('snapshot');
    request(base(node.chatId) + '/nodes/' + encodeURIComponent(node.nodeId), 'GET', undefined, abort.signal).then(data => {
      setDetail(data); setDraft(data.content); setError('');
    }).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    setNotes(branch?.value?.notes || ''); setConfirmDelete(false);
    return () => abort.abort();
  }, [selected, node?.chatId, node?.nodeId]);

  useEffect(() => {
    if (!node?.nodeId || editing || !detail || detail.revision === node.revision) return;
    const abort = new AbortController();
    request(base(node.chatId) + '/nodes/' + encodeURIComponent(node.nodeId), 'GET', undefined, abort.signal).then(data => { setDetail(data); setDraft(data.content); }).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => abort.abort();
  }, [node?.revision, editing, selected]);


  return { load };
}

function useGraphLayout(state) {
  const { graph, view, setView, nodes, setNodes, edges, setEdges, selected, setError, layoutCache, query, flow, layoutRevision, history, future, dirtyView, currentView, chatId, branch, canvasRef } = state;
  function changeView(next, record = true) {
    if (record) { history.current.push(currentView.current); if (history.current.length > 50) history.current.shift(); future.current = []; }
    dirtyView.current = true; setView(next);
  }
  function toggle(id, isGroup = false) {
    if (isGroup) {
      const v = currentView.current, ids = new Set(v.expandedGroups || []);
      if (v.showContexts || ids.has(id)) ids.delete(id); else ids.add(id);
      changeView({ ...v, showContexts: false, expandedGroups: [...ids] }); return;
    }
    const v = currentView.current, ids = new Set(v.collapsed || []);
    if (ids.has(id)) ids.delete(id); else ids.add(id);
    changeView({ ...v, collapsed: [...ids] });
  }
  useEffect(() => {
    if (!graph) return;
    const visible = visibleGraph(graph, view, query), rev = ++layoutRevision.current;
    const positionKey = visible.nodes.map(n => n.id).join('|') + visible.edges.map(e => e.id).join('|');
    async function render() {
      let positions = view.positions || {};
      if (visible.nodes.some(n => !positions[n.id])) {
        try {
          const { layoutContextGraph } = await import('./context-graph-layout.mjs');
          let automatic = layoutCache.current.get(positionKey);
          if (!automatic) { automatic = await layoutContextGraph(visible.nodes, visible.edges); layoutCache.current.set(positionKey, automatic); if (layoutCache.current.size > 8) layoutCache.current.delete(layoutCache.current.keys().next().value); }
          positions = { ...automatic, ...positions };
        } catch (e) { if (rev === layoutRevision.current) setError(e.message); return; }
      }
      if (rev !== layoutRevision.current) return;
      const parents = new Set(visible.edges.map(e => e.source));
      setNodes(visible.nodes.map(n => ({ id: n.id, type: 'context', position: positions[n.id], selected: n.id === selected || groupContains(n, selected),
        data: { ...n, toggle, canCollapse: !!n.groupItems || n.collapsed || parents.has(n.id) }, ariaLabel: `${graphNodeTitle(n)}: ${n.preview}`, style: { '--branch-color': graph.branches.find(b => b.chatId === n.chatId)?.value?.color || 'var(--wb-accent)' } })));
      setEdges(visible.edges.map(e => ({ ...e, type: 'smoothstep', label: ['fork', 'retry', 'mount', 'tools'].includes(e.kind) ? wbcT('workbenchChat.graphEdge.' + e.kind, e.kind) : undefined,
        animated: false, style: { stroke: e.kind === 'fork' ? 'var(--wb-accent)' : 'var(--wb-muted)', strokeDasharray: e.kind === 'mount' ? '5 4' : undefined } })));
      if (!flow.current?.__graphInitialized) {
        requestAnimationFrame(() => { if (flow.current) { flow.current.__graphInitialized = true; if (view.viewport) flow.current.setViewport(view.viewport); else flow.current.fitView({ nodes: readableGraphNodes(visible.nodes), padding: 0.2, minZoom: 0.8, maxZoom: 1 }); } });
      }
    }
    render();
    return () => { layoutRevision.current++; };
  }, [graph, view, query, selected]);


  useEffect(() => {
    if (!selected) return;
    const target = nodes.find(n => n.id === selected || groupContains(n.data, selected));
    if (!target || !flow.current) return;
    let frame;
    const center = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => flow.current?.fitView({ nodes: [{ id: target.id }], padding: 0.3, minZoom: 0.9, maxZoom: 1, duration: 180 })); };
    const observer = new ResizeObserver(center);
    if (canvasRef.current) observer.observe(canvasRef.current);
    center();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [selected, nodes.length]);

  return { changeView, toggle };
}

function graphNavigation(state) {
  const { view, setView, nodes, selected, setSelected, setBusy, setError, copySource, setCopySource, copyTarget, setCopyTarget, inspectionChat, setInspectionChat, setNotice, setPendingSelection, flow, viewRevision, history, future, dirtyView, chatId, dirty, load, changeView } = state;
  function choose(id, chat = inspectionChat) {
    const canonical = nodes.find(n => n.id === id || n.data.members?.some(m => m.id === id));
    if (canonical) id = canonical.id;
    if (id === selected && chat === inspectionChat) return;
    if (dirty) { setPendingSelection({ id, chat }); return; }
    setSelected(id); setInspectionChat(chat);
  }
  async function prepareCopy(source, target) {
    if (!source?.canFork || !target?.canContinue || source.id === target.id || dirty) return;
    try {
      const [from, to] = await Promise.all([request(base(source.chatId) + '/nodes/' + encodeURIComponent(source.nodeId)), request(base(target.chatId) + '/nodes/' + encodeURIComponent(target.nodeId))]);
      setCopySource({ ...source, content: from.content, projection: from.projection, revision: from.revision });
      setCopyTarget({ ...target, projection: to.projection, revision: to.revision });
    } catch (e) { setError(e.message); }
  }
  async function copyBranch() {
    setBusy(true);
    try {
      const result = await request(base(copyTarget.chatId) + '/branches', 'POST', { mode: 'graft', operationId: copyTarget.operationId || (copyTarget.operationId = crypto.randomUUID()), nodeId: copyTarget.nodeId, expectedRevision: copyTarget.revision, copiedFrom: { chatId: copySource.chatId, nodeId: copySource.nodeId, revision: copySource.revision } });
      setCopySource(null); setCopyTarget(null); setNotice(graphText('draftBranch')); await load();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function saveView() {
    const value = { ...view, layoutVersion: 2, positions: Object.fromEntries(nodes.map(n => [n.id, n.position]).concat(Object.entries(view.positions || {}).filter(([id]) => !nodes.some(n => n.id === id)))), viewport: flow.current?.getViewport(), selected, inspectionChat };
    try {
      const result = await request(base(chatId) + '/view', 'PATCH', { value, expectedRevision: viewRevision.current });
      viewRevision.current = result.revision; dirtyView.current = false; setView(value); setNotice(graphText('saved'));
    } catch (e) { setError(e.message); }
  }
  function undo(redo = false) {
    const source = redo ? future.current : history.current, target = redo ? history.current : future.current;
    if (!source.length) return; target.push(view); changeView(source.pop(), false);
  }


  return { choose, prepareCopy, copyBranch, saveView, undo };
}

function graphBranchActions(state) {
  const { setPendingSelection, dirty, setInspectionChat, graph, setSelected, detail, draft, setEditing, busy, setBusy, setError, scope, setNotice, setConfirmDelete, operation, chatId, onClose, onDirtyChange, node, branch, load } = state;
  async function activate(id, replay = false, chat = null, discard = false) {
    if (dirty && !discard) { setPendingSelection({ action: () => activate(id, replay, chat, true) }); return; }
    if (replay && !chat) { try { const result = await request("/api/workbench/chats/" + encodeURIComponent(id)); chat = result.chat || result; } catch (e) { setError(e.message); return; } }
    window.dispatchEvent(new CustomEvent('cyrene:context-graph-activate', { detail: { chatId: id, chat, replay } }));
    onClose();
  }
  async function create(mode, generate = false, content = draft) {
    if (!node || !detail || busy) return;
    const payload = { nodeId: node.nodeId, expectedRevision: detail.revision, mode, content, scope };
    const signature = JSON.stringify(payload);
    if (operation.current?.signature !== signature) operation.current = { signature, id: crypto.randomUUID() };
    setBusy(true); setError('');
    try {
      const result = await request(base(node.chatId) + '/branches', 'POST', { ...payload, operationId: operation.current.id });
      operation.current = null; setEditing(false); onDirtyChange?.(false);
      if (generate || mode === 'continue') activate(result.chat.id, generate && result.replay, result.chat, true);
      else { setNotice(graphText('draftBranch')); const fresh = await load(); const savedBranch = fresh?.branches.find(b => b.chatId === result.chat.id); setInspectionChat(result.chat.id); setSelected(savedBranch ? result.chat.id + ':' + savedBranch.displayLeafId : ''); }
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function metadata(patch) {
    if (!branch) return;
    setBusy(true);
    try { await request(base(branch.chatId) + '/metadata', 'PATCH', { value: { ...branch.value, ...patch }, expectedRevision: branch.revision }); await load(); setNotice(graphText('saved')); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function deleteBranch() {
    if (!branch) return;
    setBusy(true);
    try {
      await request('/api/workbench/chats/' + encodeURIComponent(branch.chatId), 'DELETE');
      setConfirmDelete(false); window.dispatchEvent(new CustomEvent('cyrene:wbc-refresh-chats'));
      if (branch.chatId === chatId) onClose(); else { setSelected(''); await load(); }
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  return { activate, create, metadata, deleteBranch };
}

function graphExportActions(state) {
  const { graph, nodes, edges, setBusy, error, setError, exportType, chatId } = state;
  async function exportGraph() {
    if (!graph) return;
    setBusy(true);
    try {
      if (exportType === 'svg' || exportType === 'png') {
        const svg = graphSvg(nodes, edges), blob = new Blob([svg], { type: 'image/svg+xml' });
        if (exportType === 'svg') download(blob, 'context-graph.svg');
        else {
          const image = new Image(), url = URL.createObjectURL(blob);
          try { await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = reject; image.src = url; });
            const canvas = document.createElement('canvas'); const scale = Math.min(1, 8192 / Math.max(image.width, image.height));
            canvas.width = Math.ceil(image.width * scale); canvas.height = Math.ceil(image.height * scale);
            canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
            const output = await new Promise(resolve => canvas.toBlob(resolve, 'image/png')); if (!output) throw new Error(graphText('error')); download(output, 'context-graph.png');
          } finally { URL.revokeObjectURL(url); }
        }
      } else {
        const contents = {};
        // Bound parallelism for large histories.
        const items = graph.nodes.filter(n => n.nodeId && n.chatId);
        for (let i = 0; i < items.length; i += 6) await Promise.all(items.slice(i, i + 6).map(async n => { contents[n.id] = await request(base(n.chatId) + '/nodes/' + encodeURIComponent(n.nodeId)); }));
        const data = exportType === 'json' ? JSON.stringify({ schemaVersion: 1, ...graph, contents }, null, 2) : graphMarkdown(graph, contents);
        download(new Blob([data], { type: exportType === 'json' ? 'application/json' : 'text/markdown' }), 'context-graph.' + (exportType === 'json' ? 'json' : 'md'));
      }
    } catch (e) { setError(e.message || graphText('error')); } finally { setBusy(false); }
  }

  return { exportGraph };
}

function GraphBranchSettings({ state }) {
  const { graph, busy, notes, setNotes, confirmDelete, setConfirmDelete, branch, metadata, deleteBranch } = state;
  return <details className="wbc-graph-branch-settings"><summary>{graphText('branchSettings')}</summary>
              <label>{graphText('rename')}<input key={branch.id + ':' + branch.revision} defaultValue={branch.value?.title || branch.title} onBlur={e => { if (e.target.value.trim() && e.target.value !== (branch.value?.title || branch.title)) metadata({ title: e.target.value }); }} /></label>
              <GraphBranchColor state={state} />
              <label>{graphText('notes')}<textarea value={notes} onChange={e => setNotes(e.target.value)} /></label><button disabled={busy} onClick={() => metadata({ notes })}>{graphText('saveNotes')}</button>
              <button disabled={busy} onClick={() => metadata({ favorite: !branch.value?.favorite })}>{graphText('favorite')}</button>
              <button disabled={busy} onClick={() => metadata({ archived: !branch.value?.archived })}>{graphText(branch.value?.archived ? 'restore' : 'archive')}</button>
              <button disabled={busy} onClick={() => setConfirmDelete(true)}>{graphText('delete')}</button>
              {confirmDelete && <div role="alert"><p>{graphText('deleteHint')}</p><button disabled={busy} onClick={deleteBranch}>{graphText('confirm')}</button><button onClick={() => setConfirmDelete(false)}>{graphText('cancel')}</button></div>}
            </details>;
}

function GraphNodeActions({ state, menu = false }) {
  const role = menu ? 'menuitem' : undefined;
  const { graph, view, selected, setSelected, detail, draft, setDraft, setEditing, busy, copySource, setCopySource, pinned, setPinned, setNotice, setNewContext, chatId, node, branch, changeView, prepareCopy, activate, create } = state;
  return <div className="wbc-graph-actions">
              {detail.previousContent != null && <button role={role} disabled={busy} onClick={() => create('edit', false, detail.previousContent)}><GraphActionLabel action="revert" icon={menu} /></button>}
              <button role={role} onClick={() => { changeView({ ...view, hidden: [...(view.hidden || []), selected] }); setSelected(''); }}><GraphActionLabel action="hide" icon={menu} /></button>
              {node.canEdit && <button role={role} disabled={busy} onClick={() => { setEditing(true); setDraft(detail.content); }}><GraphActionLabel action="edit" icon={menu} /></button>}
              {node.canFork && <button role={role} disabled={busy} onClick={() => { setCopySource({ ...node, content: detail.content, revision: detail.revision }); setNotice(graphText('copied')); }}><GraphActionLabel action="copyQuestion" icon={menu} /></button>}
              {copySource && node.canContinue && <button role={role} disabled={busy} onClick={() => prepareCopy(copySource, node)}><GraphActionLabel action="pasteQuestion" icon={menu} /></button>}
              {node.draft && branch?.replay && <button role={role} disabled={busy} onClick={() => activate(node.chatId, true)}><GraphActionLabel action="generate" icon={menu} /></button>}
              {node.canFork && <button role={role} disabled={busy} onClick={() => create('fork', true, detail.content)}><GraphActionLabel action="fork" icon={menu} /></button>}
              {node.canContinue && <button role={role} disabled={busy} onClick={() => create('continue', false, detail.content)}><GraphActionLabel action="continue" icon={menu} /></button>}
              {node.chatId && <button role={role} disabled={busy} onClick={() => activate(node.chatId)}><GraphActionLabel action="switch" icon={menu} /></button>}
              {node.role === 'context' && <button role={role} disabled={busy} onClick={() => create('remove', false, '')}><GraphActionLabel action="remove" icon={menu} /></button>}
              {node.canContinue && <button role={role} disabled={busy} onClick={() => { setDraft(''); setNewContext(true); setEditing(true); }}><GraphActionLabel action="addContext" icon={menu} /></button>}
              <button role={role} onClick={() => setPinned({ id: node.id, content: detail.content, projection: detail.projection, config: branch?.config })}><GraphActionLabel action="compare" icon={menu} /></button>
              {pinned && node.canContinue && <button role={role} disabled={busy} onClick={() => create('reference', false, pinned.content)}><GraphActionLabel action="reference" icon={menu} /></button>}
            </div>;
}

function GraphEditor({ state }) {
  const { graph, detail, draft, setDraft, setEditing, busy, scope, setScope, newContext, setNewContext, future, chatId, node, branch, activate, create } = state;
  return <><p>{graphText('revisionHint')}</p><textarea aria-label={graphText('edit')} value={draft} onChange={e => setDraft(e.target.value)} spellCheck={false} />
              {node.role === 'context' && <label>{graphText('scope')}<select value={scope} onChange={e => setScope(e.target.value)}><option value="snapshot">{graphText('snapshot')}</option><option value="future">{graphText('future')}</option></select></label>}
              <div className="wbc-graph-actions"><button disabled={busy} onClick={() => create(newContext ? 'reference' : 'edit')}>{graphText('save')}</button>{node.draft && branch?.replay && <button disabled={busy} onClick={() => activate(node.chatId, true)}>{graphText('generate')}</button>}
              {node.canFork && <button disabled={busy} onClick={() => create('edit', true)}>{graphText('generate')}</button>}<button disabled={busy} onClick={() => { setEditing(false); setNewContext(false); setDraft(detail.content); }}>{graphText('cancel')}</button></div>
            </>;
}

function GraphToolbar({ state }) {
  const { nodes, query, setQuery, flow, view, changeView, saveView } = state;
  return <div className="wbc-graph-toolbar">
    <input type="search" placeholder={graphText('search')} aria-label={graphText('search')} value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') flow.current?.fitView({ nodes: nodes.filter(n => n.data.match), padding: 0.2, minZoom: 0.8, maxZoom: 1 }); }} />
    <button onClick={() => flow.current?.fitView({ nodes: readableGraphNodes(nodes), padding: 0.2, minZoom: 0.8, maxZoom: 1 })}>{graphText('fit')}</button>
    <button onClick={() => flow.current?.zoomTo(1, { duration: 180 })}>100%</button>
    <button onClick={() => { flow.current.__graphInitialized = false; changeView({ ...view, positions: {}, viewport: null }); }}>{graphText('arrange')}</button>
    <button onClick={saveView}>{graphText('saveView')}</button>
    <GraphOptions state={state} />
  </div>;
}
function GraphOptions({ state }) {
  const { graph, view, busy, exportType, setExportType, flow, history, future, load, changeView, choose, exportGraph, undo } = state;
  return <details className="wbc-graph-options"><summary>{graphText('more')}<span aria-hidden="true">⌄</span></summary><div className="wbc-graph-options-panel">
    <div className="wbc-graph-option-actions"><button onClick={load}>{graphText('refresh')}</button><button onClick={() => flow.current?.fitView({ padding: 0.15 })}>{graphText('overview')}</button><button onClick={() => undo()} disabled={!history.current.length}>{graphText('undo')}</button><button onClick={() => undo(true)} disabled={!future.current.length}>{graphText('redo')}</button></div>
    <label><input type="checkbox" checked={!!view.showContexts} onChange={e => changeView({ ...view, showContexts: e.target.checked })} />{graphText('showContexts')}</label>
    <label><input type="checkbox" checked={!!view.showTools} onChange={e => changeView({ ...view, showTools: e.target.checked })} />{graphText('showTools')}</label>
    <label><input type="checkbox" checked={!!view.showArchived} onChange={e => changeView({ ...view, showArchived: e.target.checked })} />{graphText('archived')}</label>
    {!!view.hidden?.length && <button onClick={() => changeView({ ...view, hidden: [] })}>{graphText('showHidden')}</button>}
    <select aria-label={graphText('navigate')} value="" onChange={e => { changeView({ ...view, collapsed: [], hidden: [], showTools: true, showContexts: true }); choose(e.target.value); }}><option value="">{graphText('navigate')}</option>{graph?.nodes.filter(n => !n.internalCheckpoint).map(n => <option key={n.id} value={n.id}>{graphNodeTitle(n)} · {n.preview?.slice(0, 40)}</option>)}</select>
    <div className="wbc-graph-export"><select aria-label={graphText('export')} value={exportType} onChange={e => setExportType(e.target.value)}>{['json', 'markdown', 'svg', 'png'].map(k => <option key={k} value={k}>{graphText(k)}</option>)}</select><button onClick={exportGraph} disabled={busy || !graph}>{graphText('export')}</button></div>
  </div></details>;
}

function GraphCanvas({ state }) {
  const { graph, view, nodes, onNodesChange, edges, onEdgesChange, selected, detail, setEditing, flow, history, currentView, chatId, branch, changeView, choose, prepareCopy } = state;
  return <div className="wbc-graph-canvas" ref={state.canvasRef} onContextMenu={event => openGraphContextMenu(state, event)}>
        {!graph ? <p role="status">{graphText('loading')}</p> : !graph.nodes.length ? <p>{graphText('empty')}</p> : <ReactFlow
          nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
          onNodeContextMenu={(event, node) => openGraphContextMenu(state, event, node)}
          onMoveStart={event => { if (event) state.setContextMenu(null); }}
          onInit={instance => { flow.current = instance; }} onNodeClick={(_, n) => choose(n.id)}
          onNodeDoubleClick={(_, n) => { choose(n.id); if (n.id === selected && n.data.canEdit && detail) setEditing(true); }}
          onNodeDragStop={(_, n) => {
            changeView({ ...currentView.current, positions: { ...currentView.current.positions, [n.id]: n.position } });
            const target = nodes.find(other => other.id !== n.id && other.data.canContinue && Math.abs(other.position.x - n.position.x) < 120 && Math.abs(other.position.y - n.position.y) < 65);
            if (target && n.data.canFork) prepareCopy(n.data, target.data);
          }}
          nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} minZoom={0.08} maxZoom={2}
          onlyRenderVisibleElements colorMode="dark"><Background gap={22} size={1} /><Controls showInteractive={false} fitViewOptions={{ nodes: readableGraphNodes(nodes), padding: 0.2, minZoom: 0.8, maxZoom: 1 }} /><MiniMap style={{width: 136, height: 88}} pannable zoomable nodeColor={n => n.data.active ? 'var(--wb-accent)' : 'var(--wb-muted)'} /></ReactFlow>}
        <div className="wbc-graph-branch-list" aria-label={graphText('branches')}><span className="wbc-graph-branch-caption">{graphText('branches')}</span>{graph?.branches.filter(b => view.showArchived || !b.value?.archived).map(b => <button key={b.id} className={b.active ? 'active' : ''} onClick={() => {
          const tip = branchDisplayNode(nodes, b); if (tip) { choose(tip.data.groupItems ? b.chatId + ':' + (b.displayLeafId || b.leafId) : tip.id, b.chatId); flow.current?.fitView({ nodes: [{ id: tip.id }], maxZoom: 1, duration: 200 }); }
        }}>{b.value?.favorite ? '★ ' : ''}{b.value?.title || b.title || b.id}</button>)}{nodes.filter(n => n.data.tip && n.data.historical).map(n => <button key={'history:' + n.id} onClick={() => choose(n.id)}>{graphText('historical')} · {n.data.preview?.slice(0, 24)}</button>)}</div>
      </div>;
}

function GraphInspector({ state }) {
  const { graph, selected, detail, draft, editing, tab, setTab, pinned, chatId, displayNode, node, branch, choose, metadata } = state;
  if (!node || node.groupItems) return graphInspectorPlaceholder(state);
  return <aside className="wbc-graph-inspector" aria-label={graphText('details')}>
        {!node ? <p>{graphText('select')}</p> : <>
          <button className="wbc-graph-inspector-close" onClick={() => state.choose('')} aria-label={graphText('close')}>×</button><GraphMemberSelector state={state} />
          <GraphInspectorHeading node={node} />
          <div className="wbc-graph-tabs">{['details', 'input'].map(k => <button key={k} aria-pressed={tab === k} onClick={() => setTab(k)}>{graphText(k)}</button>)}</div>
          {!detail ? <p>{graphText('loading')}</p> : tab === 'input' ? <><p>{graphText('activeStats')}</p><pre>{JSON.stringify(detail.projection, null, 2)}</pre></> : <>
            {editing ? <GraphEditor state={state} /> : <><pre>{detail.content || '—'}</pre><GraphSourceDetails node={node} detail={detail} /><GraphNodeActions state={state} /></>}
            <GraphComparison state={state} />
            {branch && <GraphBranchSettings state={state} />}
          </>}
        </>}
      </aside>;
}

function GraphCopyDialog({ state }) {
  const { graph, busy, copySource, copyTarget, setCopyTarget, copyBranch } = state;
  return <div className="wbc-graph-copy-dialog" role="dialog" aria-modal="true" aria-label={graphText('copyQuestion')}><h3>{graphText('copyQuestion')}</h3><p>{graphText('copyHint')}</p><pre>{copySource.content}</pre><details><summary>{graphText('compareTitle')}</summary>{graphDiff(JSON.stringify(copySource.projection, null, 2), JSON.stringify(copyTarget.projection, null, 2)).map((line, i) => <div key={i} className="wbc-graph-copy-diff"><pre>{line.left}</pre><pre>{line.right}</pre></div>)}</details><button disabled={busy} onClick={copyBranch}>{graphText('confirm')}</button><button disabled={busy} onClick={() => setCopyTarget(null)}>{graphText('cancel')}</button></div>;
}
export function WbcContextGraph(props) {
  const data = useGraphDataState(), editor = useGraphEditorState(), refs = useGraphRequestRefs(data.view);
  const [contextMenu, setContextMenu] = useState(null);
  const state = { ...props, ...data, ...editor, ...refs, contextMenu, setContextMenu };
  Object.assign(state, graphSelection(state));
  Object.assign(state, useGraphRead(state));
  Object.assign(state, useGraphLayout(state));
  Object.assign(state, graphNavigation(state), graphBranchActions(state), graphExportActions(state));
  const { graph, selected, setSelected, editing, setEditing, error, copyTarget, inspectionChat, setInspectionChat, notice, setNotice, notes, setNotes, pendingSelection, setPendingSelection, branch, dirty } = state;
  return <div className="wbc-context-graph" onKeyDown={e => { if (e.key === 'Escape' && editing) { e.stopPropagation(); if (dirty) setPendingSelection({ id: selected, chat: inspectionChat }); else setEditing(false); } }}>
    <GraphToolbar state={state} />
    {error && <p className="wbc-graph-error" role="alert">{error}</p>}{notice && <p className="wbc-graph-notice" role="status">{notice}<button onClick={() => setNotice('')} aria-label={graphText('close')}>×</button></p>}
    <div className="wbc-graph-main">
      <GraphCanvas state={state} />
      <GraphInspector state={state} />
    </div>
    {contextMenu && <GraphRightClickMenu state={state} />}
    {copyTarget && <GraphCopyDialog state={state} />}
    {pendingSelection !== null && <div className="wbc-graph-dirty" role="alertdialog" aria-label={graphText('dirty')}><p>{graphText('dirty')}</p><button onClick={() => { discardGraphDraft(state); }}>{graphText('discard')}</button><button onClick={() => setPendingSelection(null)}>{graphText('keep')}</button></div>}
  </div>;
}

function GraphSourceDetails({ node, detail }) { return <details><summary>{graphText('sourceDetails')}</summary><pre>{JSON.stringify({ role: detail.role, time: node.createdAt, characters: node.chars, source: detail.value.context_source, metadata: detail.value.metadata, attachments: detail.value.metadata?.public_attachments, revision: detail.revisionSource, treeId: node.chatId, nodeId: node.nodeId }, null, 2)}</pre></details>; }

function GraphMemberSelector({ state }) {
 const { displayNode, node, graph, choose, selected } = state;
 return <>{displayNode?.members?.length > 1 && <select aria-label={graphText('branches')} value={node.chatId} onChange={e => { choose(selected, e.target.value); }}>{displayNode.members.map(m => <option key={m.id} value={m.chatId}>{graph.branches.find(b => b.chatId === m.chatId)?.title || m.chatId}</option>)}</select>}</>;
}

function GraphComparison({ state }) {
 const { pinned, selected, draft, detail, branch } = state;
 return <>{pinned && pinned.id !== selected && <details className="wbc-graph-diff"><summary>{graphText('compareTitle')}</summary>{graphDiff(JSON.stringify({ content: pinned.content, input: pinned.projection, configuration: pinned.config }, null, 2), JSON.stringify({ content: draft, input: detail?.projection, configuration: branch?.config }, null, 2)).map((line, i) => <div key={i} className={line.changed ? 'changed' : ''}><pre>{line.left}</pre><pre>{line.right}</pre></div>)}</details>}</>;
}

function discardGraphDraft(state) {
  state.setEditing(false);
  state.setNotes(state.branch?.value?.notes || '');
  const pending = state.pendingSelection;
  if (pending.action) pending.action();
  else { state.setSelected(pending.id); state.setInspectionChat(pending.chat); }
  state.setPendingSelection(null);
}

function GraphGroupInspector({ state }) {
  const { node, choose } = state;
  return <aside className="wbc-graph-inspector" aria-label={graphText('details')}>
    <button className="wbc-graph-inspector-close" onClick={() => choose('')} aria-label={graphText('close')}>×</button>
    <h3>{graphNodeTitle(node)}</h3><p>{graphText('groupHint')}</p>
    <div className="wbc-graph-group-list">{node.groupItems.map(item => <button key={item.id} onClick={() => inspectGroupedItem(state, item)}><strong>{graphNodeTitle(item)}</strong><span>{item.preview || graphText('emptyContent')}</span></button>)}</div>
  </aside>;
}

function groupContains(group, id) { return group.groupItems?.some(n => n.id === id || n.members?.some(m => m.id === id)); }
function inspectGroupedItem(state, item) {
  const source = item.members?.find(n => n.chatId === state.chatId) || item.members?.[0] || item;
  state.choose(source.id, source.chatId);
}
function graphInspectorPlaceholder(state) { return state.node?.groupItems ? <GraphGroupInspector state={state} /> : null; }

function graphContextLabel(kind) { const aliases = { soul: 'personality', user_language: 'language', workspace_context: 'workspace', workspace_policy: 'workspaceRules' }; return aliases[kind] ? graphText(aliases[kind]) : kind; }

function GraphInspectorHeading({ node }) { return <><h3>{graphNodeTitle(node)}</h3><small>{graphText(node.active ? 'current' : 'historical')} · {graphText('preview')}</small></>; }

function branchDisplayNode(nodes, branch) {
  const id = branch.chatId + ':' + (branch.displayLeafId || branch.leafId);
  return nodes.find(n => n.id === id || n.data.members?.some(m => m.id === id) || groupContains(n.data, id));
}

function readableGraphNodes(nodes) {
  const dialogue = nodes.filter(n => !(n.data || n).groupItems);
  return (dialogue.length ? dialogue : nodes).map(n => ({ id: n.id }));
}

function openGraphContextMenu(state, event, target = null) {
  event.preventDefault(); event.stopPropagation();
  if (state.busy || state.dirty) { state.setNotice(graphText(state.dirty ? 'dirty' : 'loading')); return; }
  if (target && target.id !== state.selected) { state.setDetail(null); state.choose(target.id); }
  const rect = state.canvasRef.current.closest('.wbc-context-graph').getBoundingClientRect();
  state.setContextMenu({ x: event.clientX - rect.left, y: event.clientY - rect.top, target });
}

function GraphRightClickMenu({ state }) {
  const { contextMenu: menu, setContextMenu, selected, detail, node, toggle } = state;
  const target = menu.target;
  return <GraphContextMenu menu={menu} close={() => setContextMenu(null)} label={graphText(target ? 'nodeMenu' : 'canvasMenu')}>

    {target ? <>
      <button role="menuitem" onClick={() => state.choose(target.id)}><GraphActionLabel action="details" /></button>
      {target.data.canCollapse && <button role="menuitem" onClick={() => toggle(target.id, !!target.data.groupItems)}><GraphActionLabel action={target.data.collapsed ? 'expandNode' : 'collapseNode'} /></button>}
      {!target.data.groupItems && <div role="separator" className="wb-item-context-separator" />}
      {selected === target.id && node && !node.groupItems && (detail ? <GraphNodeActions state={state} menu /> : <button role="menuitem" disabled><GraphActionLabel action="loading" /></button>)}
    </> : <GraphCanvasMenu state={state} />}
  </GraphContextMenu>;
}

function GraphCanvasMenu({ state }) {
  const { flow, nodes, view, changeView, saveView, load, history, future, undo } = state;
  return <>
    <button role="menuitem" onClick={() => flow.current?.fitView({ nodes: readableGraphNodes(nodes), padding: 0.2, minZoom: 0.8, maxZoom: 1 })}><GraphActionLabel action="fit" /></button>
    <button role="menuitem" onClick={() => flow.current?.fitView({ padding: 0.15 })}><GraphActionLabel action="overview" /></button>
    <button role="menuitem" onClick={() => flow.current?.zoomTo(1)}>{WBC_ICONS.search}<span>100%</span></button>
    <button role="menuitem" onClick={() => changeView({ ...view, positions: {} })}><GraphActionLabel action="arrange" /></button>
    <button role="menuitem" onClick={saveView}><GraphActionLabel action="saveView" /></button>
    <button role="menuitem" disabled={!history.current.length} onClick={() => undo()}><GraphActionLabel action="undo" /></button>
    <button role="menuitem" disabled={!future.current.length} onClick={() => undo(true)}><GraphActionLabel action="redo" /></button>
    {!!view.hidden?.length && <button role="menuitem" onClick={() => changeView({ ...view, hidden: [] })}><GraphActionLabel action="showHidden" /></button>}
    <button role="menuitem" onClick={load}><GraphActionLabel action="refresh" /></button>
  </>;
}

const GRAPH_ACTION_ICONS = {
  details: 'infoCircle', edit: 'edit', copyQuestion: 'file', pasteQuestion: 'attach',
  fork: 'fork', generate: 'play', continue: 'chat', switch: 'openExternal',
  hide: 'windowMinimize', remove: 'trash', revert: 'chevronLeft', addContext: 'plus',
  compare: 'pin', reference: 'attach', expandNode: 'chevronDown', collapseNode: 'chevronRight',
  fit: 'windowMaximize', overview: 'map', arrange: 'layers', saveView: 'download',
  undo: 'chevronLeft', redo: 'chevronRight', showHidden: 'layers', refresh: 'phase', loading: 'running',
};
function GraphActionLabel({ action, icon = true }) {
  return <>{icon && WBC_ICONS[GRAPH_ACTION_ICONS[action]]}<span>{graphText(action)}</span></>;
}

function GraphBranchColor({ state }) {
  const { branch, metadata, busy, canvasRef } = state;
  const color = branch.value?.color;
  function changeMode(event) {
    const accent = getComputedStyle(canvasRef.current).getPropertyValue('--wb-accent').trim();
    metadata({ color: event.target.checked ? '' : (/^#[0-9a-f]{6}$/i.test(accent) ? accent : '#888888') });
  }
  return <><label><span>{graphText('color')}</span><span><input type="checkbox" checked={!color} disabled={busy} onChange={changeMode} /> {graphText('followTheme')}</span></label>
    {color && <input aria-label={graphText('color')} type="color" value={color} disabled={busy} onChange={e => metadata({ color: e.target.value })} />}</>;
}
