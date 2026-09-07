import { WBC_ICONS, wbcT, wbcFormatTime, wbcSetResourceDrag, useWbcState, useWbcRef, useWbcEffect, useWbcLayoutEffect } from "../../workbench-chat.jsx"
import { WbcHoverMarquee, wbcMoveChatOrder } from "./rail-model.jsx"

function WbcTerminalStatusIcon({ stateKey, icon }) {
  var normalizedKey = String(stateKey || "status-terminal-normal").trim();
  var previousRef = useWbcRef({ key: normalizedKey, icon: icon });
  var sequenceRef = useWbcRef(0);
  var timerRef = useWbcRef(null);
  var [leaving, setLeaving] = useWbcState(null);

  useWbcLayoutEffect(function () {
    var previous = previousRef.current;
    if (previous.key === normalizedKey) {
      previous.icon = icon;
      return undefined;
    }
    sequenceRef.current += 1;
    setLeaving({
      key: previous.key + ":" + sequenceRef.current,
      stateKey: previous.key,
      icon: previous.icon,
    });
    previousRef.current = { key: normalizedKey, icon: icon };
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(function () {
      timerRef.current = null;
      setLeaving(null);
    }, 240);
    return undefined;
  }, [normalizedKey, icon]);

  useWbcEffect(function () {
    return function () {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, []);

  return <span className="wbc-terminal-status-icon">
    {leaving ? <span
      key={leaving.key}
      className={"wbc-terminal-status-glyph is-leaving " + leaving.stateKey}
    >{leaving.icon}</span> : null}
    <span
      key={normalizedKey}
      className={"wbc-terminal-status-glyph is-current " + normalizedKey}
    >{icon}</span>
  </span>;
}

  function terminalRailVisualState(terminal) {
    var status = String(terminal && terminal.status || "").trim().toLowerCase();
    var exitReason = String(terminal && terminal.exitReason || "").trim().toLowerCase();
    var processRunning = status === "running" || status === "starting";
    var failed = ["failed", "error"].indexOf(status) >= 0
      || ["pty_lost", "signal", "recovery_failed", "restart_failed"].indexOf(exitReason) >= 0
      || (status === "exited" && terminal && terminal.exitCode != null && Number(terminal.exitCode) !== 0);
    var agent = terminal && terminal.agent || null;
    var agentActive = Boolean(terminal && terminal.agentActive || agent && agent.active);
    var agentState = String(terminal && terminal.agentState || agent && agent.state || "").trim().toLowerCase();
    var agentLabel = String(terminal && terminal.agentLabel || agent && agent.label || "Agent");
    var agentStates = {
      working: { tone: " status-agent-working", icon: WBC_ICONS.agentWorking, label: wbcT("terminal.agentWorking", "Agent working") },
      waiting: { tone: " status-agent-waiting", icon: WBC_ICONS.agentWaiting, label: wbcT("terminal.agentWaiting", "Agent waiting for you") },
      completed: { tone: " status-agent-completed", icon: WBC_ICONS.agentCompleted, label: wbcT("terminal.agentCompleted", "Agent completed") },
      idle: { tone: " status-agent-idle", icon: WBC_ICONS.agentIdle, label: wbcT("terminal.agentIdle", "Agent ready") },
      failed: { tone: " status-agent-failed", icon: WBC_ICONS.errorCircle, label: wbcT("terminal.agentFailed", "Agent failed") },
      interrupted: { tone: " status-agent-interrupted", icon: WBC_ICONS.agentInterrupted, label: wbcT("terminal.agentInterrupted", "Agent interrupted") },
    };
    var agentVisual = agentActive ? agentStates[agentState] : null;
    if (agentVisual) {
      return {
        processRunning: processRunning,
        tone: agentVisual.tone,
        icon: agentVisual.icon,
        statusText: agentLabel + " · " + agentVisual.label,
      };
    }
    return {
      processRunning: processRunning,
      tone: failed ? " status-terminal-failed" : " status-terminal-normal",
      icon: failed ? WBC_ICONS.errorCircle : WBC_ICONS.slash,
      statusText: failed
        ? wbcT("terminal.statusFault", "Terminal fault")
        : wbcT("terminal.statusNormal", "Terminal normal"),
    };
  }

  export function renderTerminalCard(terminal, { menuId, terminalPinnedSet, activeTerminalId, terminalDragId, terminalOrder, setMenuId, onOpenTerminal, setTerminalDragId, setTerminalOrder, storeTerminalOrder, toggleTerminalPinned, setRenameTerminalItem, onDeleteTerminal }) {
    var id = String(terminal.id || "");
    var isMenuOpen = menuId === "terminal:" + id;
    var isPinned = terminalPinnedSet.has(id);
    var visualState = terminalRailVisualState(terminal);
    var running = visualState.processRunning;
    var unread = Boolean(terminal && terminal.unread);
    var terminalName = terminal.displayTitle || terminal.title || wbcT("terminal.title", "Terminal");
    return <div
      key={id}
      role="button"
      tabIndex={0}
      draggable="true"
      data-terminal-id={id}
      data-cyrene-context-menu="true"
      className={"wbc-chat-card wbc-terminal-card wbc-project-resource-card"
        + (String(activeTerminalId || "") === id ? " active" : "")
        + (isMenuOpen ? " menu-open" : "")
        + (terminalDragId === id ? " dragging" : "")
        + (unread ? " has-unread" : "")
        + visualState.tone}
      aria-label={terminalName + "，" + visualState.statusText + (unread ? "，" + wbcT("terminal.unread", "Unread") : "")}
      onClick={function () { setMenuId(""); if (onOpenTerminal) onOpenTerminal(id); }}
      onKeyDown={function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          if (onOpenTerminal) onOpenTerminal(id);
        }
      }}
      onContextMenu={function (event) {
        event.preventDefault();
        event.stopPropagation();
        setMenuId("terminal:" + id);
      }}
      onDragStart={function (event) {
        if (event.target && event.target.closest && event.target.closest("button")) {
          event.preventDefault();
          return;
        }
        setMenuId("");
        setTerminalDragId(id);
        wbcSetResourceDrag(event, { kind: "terminal", terminalId: id, title: terminal.displayTitle || terminal.title || "Terminal" });
      }}
      onDragOver={function (event) {
        if (!terminalDragId || terminalDragId === id) return;
        event.preventDefault();
        event.stopPropagation();
        var rect = event.currentTarget.getBoundingClientRect();
        var edge = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
        setTerminalOrder(function (current) { return wbcMoveChatOrder(current, terminalDragId, id, edge); });
      }}
      onDrop={function (event) {
        if (!terminalDragId) return;
        event.preventDefault();
        event.stopPropagation();
        storeTerminalOrder(terminalOrder);
        setTerminalDragId("");
      }}
      onDragEnd={function () { storeTerminalOrder(terminalOrder); setTerminalDragId(""); }}
    >
      <span className="wbc-chat-card-top">
        <span className="wbc-chat-row-icon" aria-hidden="true" title={visualState.statusText}>
          <WbcTerminalStatusIcon stateKey={visualState.tone} icon={visualState.icon} />
          {unread ? <i className="wbc-terminal-unread-dot" /> : null}
        </span>
        <span className="wbc-chat-card-title">
          {isPinned && <span className="wbc-chat-card-pin" aria-hidden="true">{WBC_ICONS.pin}</span>}
          <b><WbcHoverMarquee text={terminal.displayTitle || terminal.title || wbcT("terminal.title", "Terminal")} /></b>
        </span>
        <span className="wbc-chat-card-right">
          <time className="wbc-chat-card-time">{wbcFormatTime(terminal.updatedAt || terminal.createdAt)}</time>
          <span className="wbc-chat-card-actions">
            <button type="button" className="wb-card-menu-btn wbc-chat-card-menu-btn" onClick={function (event) {
              event.stopPropagation();
              setMenuId(isMenuOpen ? "" : "terminal:" + id);
            }} aria-label={wbcT("common.moreActions", "More actions")}>{WBC_ICONS.dots}</button>
            {isMenuOpen && <div className="wb-card-menu" role="menu">
              <button type="button" role="menuitem" className="wbc-chat-pin-action" onClick={function (event) { event.stopPropagation(); setMenuId(""); toggleTerminalPinned(id); }}>
                <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.pin}</span>
                <span>{isPinned ? wbcT("terminal.unpin", "Unpin terminal") : wbcT("terminal.pin", "Pin terminal")}</span>
              </button>
              <button type="button" role="menuitem" className="wbc-chat-menu-action" onClick={function (event) { event.stopPropagation(); setMenuId(""); setRenameTerminalItem(terminal); }}>
                <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span>
                <span>{wbcT("terminal.rename", "Rename terminal")}</span>
              </button>
              <button type="button" role="menuitem" className="wbc-chat-menu-action danger" onClick={function (event) { event.stopPropagation(); setMenuId(""); if (onDeleteTerminal) onDeleteTerminal(id); }}>
                <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.trash}</span>
                <span>{wbcT("terminal.delete", "Delete terminal")}</span>
              </button>
            </div>}
          </span>
        </span>
      </span>
      <span className="wbc-chat-card-preview">
        {running ? <i className="wbc-running-dot" /> : null}
        <WbcHoverMarquee text={(terminal && terminal.agentActive && terminal.agentState)
          ? visualState.statusText
          : (running ? String(terminal.cwd || "") : wbcT("terminal.exited", "Process exited"))} />
      </span>
    </div>;
  }

