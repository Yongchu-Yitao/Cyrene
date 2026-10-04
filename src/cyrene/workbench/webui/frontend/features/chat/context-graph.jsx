import { ReactFlow, Background, Controls, MiniMap, Handle, Position, useNodesState, useEdgesState } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './context-graph.css';
import { wbcT, useWbcState as useState, useWbcEffect as useEffect, useWbcRef as useRef } from './core.jsx';
import { visibleGraph, graphMarkdown, graphDiff, graphSvg } from './context-graph-model.mjs';
import { WBC_ICONS } from './icons.jsx';
import { GraphContextMenu } from './context-graph-menu.jsx';
import { WbcContextInspectorContent } from './context-indicator.jsx';
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
function graphToolPhase(data) { return data.toolPhase || (data.toolGroup ? data.role === 'tool_results' ? 'result' : 'call' : ''); }
function graphNodeClass(data) { return 'wbc-graph-node' + (data.active ? ' is-active' : '') + (data.match ? ' is-match' : '') + (data.historical ? ' is-history' : '') + (data.groupItems ? ' is-context-group' : '') + (graphToolPhase(data) ? ' is-tool-' + graphToolPhase(data) : ''); }
function graphNodePreview(data) {
  const phase = graphToolPhase(data);
  if (!phase) return data.preview || graphText('start');
  if (data.toolNames?.length) return data.preview;
  const names = [...String(data.preview || '').matchAll(/"name"\s*:\s*"([^"]+)"/g)].map(match => match[1]);
  return [...new Set(names)].slice(0, 3).join(' · ') || graphText(phase === 'call' ? 'toolCall' : 'toolResult');
}
function graphNodeBadge(data) { if (data.draft) return graphText('draft'); if (data.retry) return '↻'; if (data.manual) return graphText('manual'); return data.tip ? '●' : ''; }
function graphNodeTitle(data) {
  if (data.groupItems) return graphText(data.groupKind) + ' · ' + data.groupItems.length;
  const phase = graphToolPhase(data);
  if (phase) return graphText(phase === 'call' ? 'toolCall' : 'toolResult') + (data.toolCount > 1 ? ' · ' + data.toolCount : '');
  if (data.role === 'task') return graphText('taskDocument');
  if (data.role === 'context') return wbcT('workbenchChat.contextKind.' + data.kind, graphContextLabel(data.kind));
  return wbcT('workbenchChat.contextRole.' + data.role, data.role);
}
function ContextNode({ data }) {
  return <div className={graphNodeClass(data)}>
    <Handle type="target" position={Position.Left} isConnectable={false} />
    <div className="wbc-graph-node-head"><b>{graphNodeTitle(data)}</b><span>{graphNodeBadge(data)}</span></div>
    <p>{data.groupItems ? data.groupItems.map(graphNodeTitle).join(' · ') : graphNodePreview(data)}</p><small>{data.groupItems ? graphText('inspectGroup') : data.pending ? graphText('pending') : data.kind || ''}</small>
    {data.canCollapse && <button type="button" className="nodrag" aria-label={graphText('collapse')} onClick={e => { e.stopPropagation(); data.toggle(data.id, !!data.groupItems); }}>{data.collapsed ? '+' : '−'}</button>}
    <Handle type="source" position={Position.Right} isConnectable={false} />
  </div>;
}
const nodeTypes = { context: ContextNode };

function useGraphDataState() {
  const [graph, setGraph] = useState(null), [view, setView] = useState({ positions: {}, collapsed: [], showTools: true });
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
        const savedView = result.view.value.layoutVersion === 2 ? result.view.value : {};
        const revealTools = savedView.showTools === undefined;
        setView({ positions: {}, collapsed: [], ...savedView, showTools: savedView.showTools !== false,
          ...(revealTools ? { positions: {}, viewport: null } : {}) });
        setSelected(result.view.value.selected || ''); setInspectionChat(result.view.value.inspectionChat || ''); dirtyView.current = false;
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

function useGraphAudit(state) {
  const [audit, setAudit] = useState(null), [auditError, setAuditError] = useState(''), [auditLoading, setAuditLoading] = useState(false);
  const [auditRevision, setAuditRevision] = useState(0);
  const selectedChatId = state.node?.chatId;
  useEffect(() => {
    if (!selectedChatId) { setAudit(null); setAuditError(''); setAuditLoading(false); return; }
    const abort = new AbortController();
    setAudit(null); setAuditError(''); setAuditLoading(true);
    request('/api/workbench/chats/' + encodeURIComponent(selectedChatId) + '/context', 'GET', undefined, abort.signal)
      .then(data => { setAudit(data); setAuditLoading(false); })
      .catch(error => { if (error.name !== 'AbortError') { setAuditError(error.message); setAuditLoading(false); } });
    return () => abort.abort();
  }, [selectedChatId, auditRevision]);
  return { audit, auditError, auditLoading, reloadAudit: () => setAuditRevision(value => value + 1) };
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
      const visibleById = new Map(visible.nodes.map(n => [n.id, n]));
      const toolResultBySource = new Map(visible.edges.map(e => [e.source, visibleById.get(e.target)]).filter(([, target]) => target?.role === 'tool_results'));
      setNodes(visible.nodes.map(n => {
        const result = toolResultBySource.get(n.id);
        const display = n.toolGroup && n.role === 'assistant' && !n.preview && result
          ? { ...n, preview: graphNodePreview(result), toolNames: [graphNodePreview(result)] } : n;
        return { id: n.id, type: 'context', position: positions[n.id], selected: n.id === selected || groupContains(n, selected),
          data: { ...display, toggle, canCollapse: !!n.groupItems || n.collapsed || parents.has(n.id) }, ariaLabel: `${graphNodeTitle(display)}: ${graphNodePreview(display)}`, style: { '--branch-color': graph.branches.find(b => b.chatId === n.chatId)?.value?.color || 'var(--wb-accent)' } };
      }));
      setEdges(visible.edges.map(e => ({ ...e, type: 'smoothstep', label: ['fork', 'retry', 'mount', 'tools'].includes(e.kind) ? wbcT('workbenchChat.graphEdge.' + e.kind, e.kind) : undefined,
        animated: false, style: { stroke: e.kind === 'fork' ? 'var(--wb-accent)' : 'var(--wb-muted)', strokeDasharray: e.kind === 'mount' ? '5 4' : undefined } })));
      if (!flow.current?.__graphInitialized) {
        requestAnimationFrame(() => { if (flow.current) { flow.current.__graphInitialized = true; if (view.viewport) flow.current.setViewport(view.viewport); else flow.current.fitView({ nodes: readableGraphNodes(visible.nodes).slice(0, 4), padding: 0.15, minZoom: 0.65, maxZoom: 1 }); } });
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
  return <details className="wbc-graph-branch-settings wbc-graph-disclosure"><GraphDisclosureSummary>{graphText('branchSettings')}</GraphDisclosureSummary>
              <label>{graphText('rename')}<input key={branch.id + ':' + branch.revision} defaultValue={branch.value?.title || branch.title} onBlur={e => { if (e.target.value.trim() && e.target.value !== (branch.value?.title || branch.title)) metadata({ title: e.target.value }); }} /></label>
              <GraphBranchColor state={state} />
              <label>{graphText('notes')}<textarea value={notes} onChange={e => setNotes(e.target.value)} /></label><button disabled={busy} onClick={() => metadata({ notes })}>{graphText('saveNotes')}</button>
              <button disabled={busy} onClick={() => metadata({ favorite: !branch.value?.favorite })}>{graphText('favorite')}</button>
              <button disabled={busy} onClick={() => metadata({ archived: !branch.value?.archived })}>{graphText(branch.value?.archived ? 'restore' : 'archive')}</button>
              <button disabled={busy} onClick={() => setConfirmDelete(true)}>{graphText('delete')}</button>
              {confirmDelete && <div role="alert"><p>{graphText('deleteHint')}</p><button disabled={busy} onClick={deleteBranch}>{graphText('confirm')}</button><button onClick={() => setConfirmDelete(false)}>{graphText('cancel')}</button></div>}
            </details>;
}

function GraphNodeActions({ state, menu = false, secondary = false }) {
  const role = menu ? 'menuitem' : undefined;
  const { graph, view, selected, setSelected, detail, draft, setDraft, setEditing, busy, copySource, setCopySource, pinned, setPinned, setNotice, setNewContext, chatId, node, branch, changeView, prepareCopy, activate, create } = state;
  const actionLabel = action => <GraphActionLabel action={action} icon={menu || secondary} hint={secondary} />;
  return <div className={'wbc-graph-actions' + (secondary ? ' is-secondary' : '')}>
              {detail.previousContent != null && <button role={role} disabled={busy} onClick={() => create('edit', false, detail.previousContent)}>{actionLabel('revert')}</button>}
              <button role={role} onClick={() => { changeView({ ...view, hidden: [...(view.hidden || []), selected] }); setSelected(''); }}>{actionLabel('hide')}</button>
              {!secondary && node.canEdit && <button role={role} disabled={busy} onClick={() => { setEditing(true); setDraft(detail.content); }}>{actionLabel('edit')}</button>}
              {node.canFork && <button role={role} disabled={busy} onClick={() => { setCopySource({ ...node, content: detail.content, revision: detail.revision }); setNotice(graphText('copied')); }}>{actionLabel('copyQuestion')}</button>}
              {copySource && node.canContinue && <button role={role} disabled={busy} onClick={() => prepareCopy(copySource, node)}>{actionLabel('pasteQuestion')}</button>}
              {node.draft && branch?.replay && <button role={role} disabled={busy} onClick={() => activate(node.chatId, true)}>{actionLabel('generate')}</button>}
              {node.canFork && <button role={role} disabled={busy} onClick={() => create('fork', true, detail.content)}>{actionLabel('fork')}</button>}
              {node.canContinue && <button role={role} disabled={busy} onClick={() => create('continue', false, detail.content)}>{actionLabel('continue')}</button>}
              {!secondary && node.chatId && <button role={role} disabled={busy} onClick={() => activate(node.chatId)}>{actionLabel('switch')}</button>}
              {node.role === 'context' && <button role={role} disabled={busy} onClick={() => create('remove', false, '')}>{actionLabel('remove')}</button>}
              {node.canContinue && <button role={role} disabled={busy} onClick={() => { setDraft(''); setNewContext(true); setEditing(true); }}>{actionLabel('addContext')}</button>}
              <button role={role} className={pinned?.id === node.id ? 'is-selected' : ''} aria-pressed={menu ? undefined : pinned?.id === node.id} onClick={() => setPinned(pinned?.id === node.id ? null : { id: node.id, content: detail.content, projection: detail.projection, config: branch?.config })}>{actionLabel(pinned?.id === node.id ? 'clearCompare' : 'compare')}</button>
              {pinned && node.canContinue && <button role={role} disabled={busy} onClick={() => create('reference', false, pinned.content)}>{actionLabel('reference')}</button>}
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
  const { nodes, query, setQuery, flow } = state;
  return <div className="wbc-graph-toolbar">
    <input type="search" placeholder={graphText('search')} aria-label={graphText('search')} value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') flow.current?.fitView({ nodes: nodes.filter(n => n.data.match), padding: 0.2, minZoom: 0.8, maxZoom: 1 }); }} />
    <GraphOptions state={state} />
    <GraphBranchNavigation state={state} />
  </div>;
}
function GraphOptions({ state }) {
  const { graph, view, nodes, busy, exportType, setExportType, flow, history, future, load, changeView, choose, exportGraph, saveView, undo } = state;
  return <details className="wbc-graph-options"><summary>{graphText('more')}<span className="wbc-graph-caret" aria-hidden="true">{WBC_ICONS.chevronDown}</span></summary><div className="wbc-graph-options-panel">
    <div className="wbc-graph-options-section"><div className="wbc-graph-options-heading">{graphText('canvasMenu')}</div><div className="wbc-graph-option-actions">
      <button onClick={() => flow.current?.fitView({ nodes: readableGraphNodes(nodes), padding: 0.2, minZoom: 0.3, maxZoom: 1 })}><GraphActionLabel action="fit" /></button>
      <button onClick={() => flow.current?.zoomTo(1, { duration: 180 })}><span className="wbc-graph-option-scale-icon" aria-hidden="true">1:1</span><span>100%</span></button>
      <button onClick={() => { if (flow.current) flow.current.__graphInitialized = false; changeView({ ...view, positions: {}, viewport: null }); }}><GraphActionLabel action="arrange" /></button>
      <button onClick={saveView}><GraphActionLabel action="saveView" /></button>
      <button onClick={load}><GraphActionLabel action="refresh" /></button>
      <button onClick={() => flow.current?.fitView({ padding: 0.15 })}><GraphActionLabel action="overview" /></button>
      <button onClick={() => undo()} disabled={!history.current.length}><GraphActionLabel action="undo" /></button>
      <button onClick={() => undo(true)} disabled={!future.current.length}><GraphActionLabel action="redo" /></button>
    </div></div>
    <div className="wbc-graph-options-section"><div className="wbc-graph-options-heading">{graphText('displayOptions')}</div><div className="wbc-graph-option-toggles">
      <label><input type="checkbox" checked={!!view.showContexts} onChange={e => changeView({ ...view, showContexts: e.target.checked })} />{graphText('showContexts')}</label>
      <label><input type="checkbox" checked={view.showTools !== false} onChange={e => { if (flow.current) flow.current.__graphInitialized = false; changeView({ ...view, showTools: e.target.checked, positions: {}, viewport: null }); }} />{graphText('showTools')}</label>
      <label><input type="checkbox" checked={!!view.showArchived} onChange={e => changeView({ ...view, showArchived: e.target.checked })} />{graphText('archived')}</label>
      {!!view.hidden?.length && <button onClick={() => changeView({ ...view, hidden: [] })}><GraphActionLabel action="showHidden" /></button>}
    </div></div>
    <div className="wbc-graph-options-footer"><select aria-label={graphText('navigate')} value="" onChange={e => { changeView({ ...view, collapsed: [], hidden: [], showTools: true, showContexts: true }); choose(e.target.value); }}><option value="">{graphText('navigate')}</option>{graph?.nodes.filter(n => !n.internalCheckpoint).map(n => <option key={n.id} value={n.id}>{graphNodeTitle(n)} · {n.preview?.slice(0, 40)}</option>)}</select>
    <div className="wbc-graph-export"><select aria-label={graphText('export')} value={exportType} onChange={e => setExportType(e.target.value)}>{['json', 'markdown', 'svg', 'png'].map(k => <option key={k} value={k}>{graphText(k)}</option>)}</select><button onClick={exportGraph} disabled={busy || !graph}>{graphText('export')}</button></div></div>
  </div></details>;
}

function GraphCanvas({ state }) {
  const { graph, nodes, onNodesChange, edges, onEdgesChange, selected, detail, setEditing, flow, currentView, changeView, choose, prepareCopy } = state;
  return <div className="wbc-graph-canvas" ref={state.canvasRef} onContextMenu={event => openGraphContextMenu(state, event)}>
        {!graph ? <p role="status">{graphText('loading')}</p> : !graph.nodes.length ? <p>{graphText('empty')}</p> : <ReactFlow
          nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
          onNodeContextMenu={(event, node) => openGraphContextMenu(state, event, node)}
          onMoveStart={event => { if (event) state.setContextMenu(null); }}
          onInit={instance => { flow.current = instance; }} onNodeClick={(_, n) => choose(n.id)}
          onNodeDoubleClick={(_, n) => { choose(n.id); if (n.id === selected && n.data.canEdit && detail) setEditing(true); }}
          onNodeDragStop={(_, n) => {
            changeView({ ...currentView.current, positions: { ...currentView.current.positions, [n.id]: n.position } });
            const target = nodes.find(other => other.id !== n.id && n.data.canFork && other.data.canContinue && Math.abs(other.position.x - n.position.x) < 120 && Math.abs(other.position.y - n.position.y) < 65);
            if (target) prepareCopy(n.data, target.data);
          }}
          nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} minZoom={0.08} maxZoom={2} proOptions={{ hideAttribution: true }}
          onlyRenderVisibleElements colorMode="dark"><Background gap={22} size={1} /><Controls showInteractive={false} fitViewOptions={{ nodes: readableGraphNodes(nodes), padding: 0.2, minZoom: 0.3, maxZoom: 1 }} /><MiniMap style={{width: 136, height: 88}} pannable zoomable nodeColor={n => n.data.active ? 'var(--wb-accent)' : 'var(--wb-muted)'} /></ReactFlow>}
      </div>;
}

function GraphBranchNavigation({ state }) {
  const { graph, view, nodes, selected, choose, flow } = state;
  const branches = graph?.branches.filter(b => view.showArchived || !b.value?.archived) || [];
  const titleCounts = new Map();
  for (const b of branches) {
    const title = b.value?.title || b.title || b.id;
    titleCounts.set(title, (titleCounts.get(title) || 0) + 1);
  }
  const titleOrdinals = new Map();
  const historicalTips = nodes.filter(n => n.data.tip && n.data.historical);
  return <nav className="wbc-graph-branch-list" aria-label={graphText('branches')}>
          <div className="wbc-graph-branch-items"><span className="wbc-graph-branch-caption">{graphText('branches')} <b>{branches.length}</b></span>
          {branches.map(b => {
            const title = b.value?.title || b.title || b.id;
            const ordinal = (titleOrdinals.get(title) || 0) + 1;
            titleOrdinals.set(title, ordinal);
            const label = titleCounts.get(title) > 1 ? `${title} · ${ordinal}` : title;
            return <button key={b.id} type="button" title={label} aria-current={b.active ? 'page' : undefined} className={b.active ? 'active' : ''} onClick={() => {
              const tip = branchDisplayNode(nodes, b); if (tip) { choose(tip.data.groupItems ? b.chatId + ':' + (b.displayLeafId || b.leafId) : tip.id, b.chatId); flow.current?.fitView({ nodes: [{ id: tip.id }], maxZoom: 1, duration: 200 }); }
            }}>{b.value?.favorite ? '★ ' : ''}{label}</button>;
          })}</div>
          {!!historicalTips.length && <details className="wbc-graph-history"><summary>{graphText('historical')} <b>{historicalTips.length}</b><span className="wbc-graph-caret" aria-hidden="true">{WBC_ICONS.chevronDown}</span></summary>
            <div className="wbc-graph-history-menu">{historicalTips.map((n, i) => {
              const time = n.data.updatedAt ? new Date(n.data.updatedAt).toLocaleString() : '';
              const label = `${i + 1}. ${n.data.preview || graphNodeTitle(n)}`;
              return <button key={n.id} type="button" title={label} aria-current={selected === n.id ? 'location' : undefined} onClick={e => { choose(n.id, n.data.chatId); e.currentTarget.closest('details').open = false; }}><span>{label}</span>{time && <small>{time}</small>}</button>;
            })}</div>
          </details>}
        </nav>;
}

function GraphInspector({ state }) {
  const { detail, editing, node, branch, audit, auditError, auditLoading, reloadAudit, load, busy, setEditing, setDraft, activate } = state;
  if (!node || node.groupItems) return graphInspectorPlaceholder(state);
  const selectedAuditItem = audit?.timeline?.find(item => String(item.nodeId || item.id || '') === String(node.nodeId || ''));
  const toolPhase = graphToolPhase(node);
  const branchTitle = branch?.value?.title || branch?.title || node.chatId;
  return <aside className="wbc-graph-inspector" aria-label={graphText('details')}>
        <div className="wbc-graph-inspector-head">
          <div><GraphInspectorHeading node={node} /><span className="wbc-graph-inspector-branch" title={branchTitle}>{branchTitle}</span></div>
          <button className="wbc-graph-inspector-close" onClick={() => state.choose('')} aria-label={graphText('close')}>{WBC_ICONS.x}</button>
        </div>
        <GraphMemberSelector state={state} />
        {!detail ? <p className="wbc-graph-inspector-content" role="status">{graphText('loading')}</p> : <div className="wbc-graph-inspector-content">
          {editing ? <GraphEditor state={state} /> : <>
            {toolPhase && <GraphToolDetails node={node} value={detail.value} />}
            {((detail.content && toolPhase !== 'result') || !toolPhase) && <section className="wbc-graph-detail-section"><h4>{graphText('nodeContent')}</h4><pre className="wbc-graph-node-content">{detail.content || '—'}</pre></section>}
            <div className="wbc-graph-primary-actions">
              {node.canEdit && <button disabled={busy} onClick={() => { setEditing(true); setDraft(detail.content); }}>{graphText('edit')}</button>}
              {node.chatId && <button disabled={busy} onClick={() => activate(node.chatId)}>{graphText('switch')}</button>}
            </div>
            <GraphModelMessages projection={detail.projection} />
            {selectedAuditItem && <div className="wbc-graph-selected-audit"><span>{graphText('nodeEstimate')}</span><strong>{selectedAuditItem.tokensEst} {wbcT('workbenchChat.contextAuditTokensUnit', 'tokens')}</strong><small>{selectedAuditItem.chars} {wbcT('workbenchChat.contextAuditCharsUnit', 'characters')}{selectedAuditItem.activeMount ? ' · ' + wbcT('workbenchChat.contextAuditActiveMount', 'Current mount') : ''}</small></div>}
            <details className="wbc-graph-more-actions wbc-graph-disclosure"><GraphDisclosureSummary>{graphText('moreActions')}</GraphDisclosureSummary><GraphNodeActions state={state} secondary /></details>
            <GraphSourceDetails node={node} detail={detail} />
            <details className="wbc-graph-branch-audit wbc-graph-disclosure"><GraphDisclosureSummary>{graphText('branchAudit')}</GraphDisclosureSummary>
              <p>{graphText('inputScope')}: <strong>{branchTitle}</strong></p>
              {auditLoading ? <p role="status">{graphText('loading')}</p> : auditError ? <div className="wbc-graph-audit-message" role="alert">{auditError}<button type="button" onClick={reloadAudit}>{graphText('refresh')}</button></div> : audit ? <>{!audit.timeline?.length && <p className="wbc-graph-audit-message">{graphText('noAuditTimeline')}</p>}<WbcContextInspectorContent data={audit} chatId={node.chatId} selectedNodeId={node.nodeId} showTimeline={!!audit.timeline?.length} onReload={() => { reloadAudit(); load(); }} /></> : null}
            </details>
          </>}
          <GraphComparison state={state} />
          {branch && <GraphBranchSettings state={state} />}
        </div>}
      </aside>;
}

function GraphModelMessages({ projection }) {
  const messages = Array.isArray(projection) ? projection : [];
  return <section className="wbc-graph-detail-section wbc-graph-model-messages">
    <h4>{graphText('selectedPath')} <span>{messages.length}</span></h4>
    {messages.length ? <ol>{messages.map((message, index) => {
      const role = wbcT('workbenchChat.contextRole.' + message.role, message.role || graphText('message'));
      const content = typeof message.content === 'string' ? message.content : JSON.stringify(message.content ?? '', null, 2);
      return <li key={index}><details open={index === messages.length - 1}><summary><b>{index + 1}. {role}</b><span className="wbc-graph-message-preview">{content.slice(0, 80) || graphText('emptyContent')}</span><span className="wbc-graph-disclosure-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span></summary><pre>{content || graphText('emptyContent')}</pre>{Object.keys(message).some(key => !['role', 'content'].includes(key)) && <pre className="wbc-graph-message-extra">{JSON.stringify(Object.fromEntries(Object.entries(message).filter(([key]) => !['role', 'content'].includes(key))), null, 2)}</pre>}</details></li>;
    })}</ol> : <p>{graphText('noMessages')}</p>}
  </section>;
}

function GraphToolDetails({ node, value }) {
  const phase = graphToolPhase(node);
  const items = phase === 'call' ? value?.tool_calls : value?.results;
  const entries = Array.isArray(items) ? items.filter(item => item && typeof item === 'object') : [];
  return <section className="wbc-graph-detail-section wbc-graph-tool-details">
    <h4>{graphText(phase === 'call' ? 'toolCall' : 'toolResult')} <span>{entries.length}</span></h4>
    {entries.map((item, index) => {
      const name = String(item.name || graphText('tools'));
      const label = wbcT('toolName.' + name, name);
      const payload = phase === 'call' ? item.arguments : item.value;
      const content = typeof payload === 'string' ? payload : JSON.stringify(payload ?? {}, null, 2);
      return <details key={item.id || item.call_id || index} open={entries.length === 1}>
        <summary><strong>{label}</strong>{phase === 'result' && <small className={item.success === false ? 'is-error' : ''}>{graphText(item.success === false ? 'toolFailed' : 'toolCompleted')}</small>}<span className="wbc-graph-disclosure-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span></summary>
        <pre>{content}</pre>
        {item.error && <p className="wbc-graph-tool-error">{String(item.error)}</p>}
      </details>;
    })}
  </section>;
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
  Object.assign(state, useGraphAudit(state));
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

function GraphDisclosureSummary({ children }) { return <summary><span className="wbc-graph-disclosure-label">{children}</span><span className="wbc-graph-disclosure-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span></summary>; }

function GraphSourceDetails({ node, detail }) { return <details className="wbc-graph-disclosure"><GraphDisclosureSummary>{graphText('sourceDetails')}</GraphDisclosureSummary><pre>{JSON.stringify({ role: detail.role, time: node.createdAt, characters: node.chars, source: detail.value.context_source, metadata: detail.value.metadata, attachments: detail.value.metadata?.public_attachments, revision: detail.revisionSource, treeId: node.chatId, nodeId: node.nodeId }, null, 2)}</pre></details>; }

function GraphMemberSelector({ state }) {
 const { displayNode, node, graph, choose, selected } = state;
 return <>{displayNode?.members?.length > 1 && <select aria-label={graphText('branches')} value={node.chatId} onChange={e => { choose(selected, e.target.value); }}>{displayNode.members.map(m => <option key={m.id} value={m.chatId}>{graph.branches.find(b => b.chatId === m.chatId)?.title || m.chatId}</option>)}</select>}</>;
}

function GraphComparison({ state }) {
 const { pinned, selected, draft, detail, branch } = state;
 return <>{pinned && pinned.id !== selected && <details className="wbc-graph-diff wbc-graph-disclosure"><GraphDisclosureSummary>{graphText('compareTitle')}</GraphDisclosureSummary>{graphDiff(JSON.stringify({ content: pinned.content, input: pinned.projection, configuration: pinned.config }, null, 2), JSON.stringify({ content: draft, input: detail?.projection, configuration: branch?.config }, null, 2)).map((line, i) => <div key={i} className={line.changed ? 'changed' : ''}><pre>{line.left}</pre><pre>{line.right}</pre></div>)}</details>}</>;
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
    <div className="wbc-graph-inspector-head">
      <h3>{graphNodeTitle(node)}</h3>
      <button className="wbc-graph-inspector-close" onClick={() => choose('')} aria-label={graphText('close')}>{WBC_ICONS.x}</button>
    </div>
    <div className="wbc-graph-inspector-content wbc-graph-group-content">
      <p>{graphText('groupHint')}</p>
      <div className="wbc-graph-group-list">{node.groupItems.map(item => <button key={item.id} onClick={() => inspectGroupedItem(state, item)}><strong>{graphNodeTitle(item)}</strong><span>{item.preview || graphText('emptyContent')}</span></button>)}</div>
    </div>
  </aside>;
}

function groupContains(group, id) { return group.groupItems?.some(n => n.id === id || n.members?.some(m => m.id === id)); }
function inspectGroupedItem(state, item) {
  const source = item.members?.find(n => n.chatId === state.chatId) || item.members?.[0] || item;
  state.choose(source.id, source.chatId);
}
function graphInspectorPlaceholder(state) { return state.node?.groupItems ? <GraphGroupInspector state={state} /> : null; }

function graphContextLabel(kind) { const aliases = { soul: 'personality', user_language: 'language', workspace_context: 'workspace', workspace_policy: 'workspaceRules' }; return aliases[kind] ? graphText(aliases[kind]) : kind; }

function GraphInspectorHeading({ node }) { return <><h3>{graphNodeTitle(node)}</h3><small>{graphText(node.active ? 'current' : 'historical')}</small></>; }

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
    <button role="menuitem" onClick={() => flow.current?.fitView({ nodes: readableGraphNodes(nodes), padding: 0.2, minZoom: 0.3, maxZoom: 1 })}><GraphActionLabel action="fit" /></button>
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
  compare: 'pin', clearCompare: 'pin', reference: 'attach', expandNode: 'chevronDown', collapseNode: 'chevronRight',
  fit: 'windowMaximize', overview: 'map', arrange: 'layers', saveView: 'download',
  undo: 'chevronLeft', redo: 'chevronRight', showHidden: 'layers', refresh: 'phase', loading: 'running',
};
function GraphActionLabel({ action, icon = true, hint = false }) {
  if (hint) return <>{icon && <span className="wbc-graph-action-icon" aria-hidden="true">{WBC_ICONS[GRAPH_ACTION_ICONS[action]]}</span>}<span className="wbc-graph-action-copy"><strong>{graphText(action)}</strong><small>{graphText(action + 'Hint')}</small></span></>;
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
