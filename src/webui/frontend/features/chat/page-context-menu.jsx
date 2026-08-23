import { workbenchServices } from "../../shared/runtime/services.jsx"
import { wbcCanOpenPageContextMenu, wbcNotifyBrowserWindowInteraction, wbcPageContextMenuPlacement, wbcT } from "../../workbench-chat.jsx"
import { WbcQuickActionItems } from "./context-panel.jsx"

function WbcChatPageContextMenu({
  menu,
  chat,
  onClose,
  onRename,
  onDelete,
  onToTask,
  toTaskBusy,
  onCompact,
  compactBusy,
  onGenerateMemory,
  memoryLearningBusy,
}) {
  if (!menu || !chat) return null;
  return (
    <div className="wb-item-context-layer wbc-page-context-layer">
      <div className="wb-item-context-scrim" onPointerDown={onClose} />
      <div
        className="wb-item-context-menu wbc-page-context-menu"
        role="menu"
        aria-label={wbcT("workbenchChat.quickActions", "Quick actions")}
        style={{ left: menu.left + "px", top: menu.top + "px" }}
        onContextMenu={function (event) { event.preventDefault(); }}
      >
        <WbcQuickActionItems
          chat={chat}
          menu={true}
          onBeforeAction={onClose}
          onRename={onRename}
          onDelete={onDelete}
          onToTask={onToTask}
          toTaskBusy={toTaskBusy}
          onCompact={onCompact}
          compactBusy={compactBusy}
          onGenerateMemory={onGenerateMemory}
          memoryLearningBusy={memoryLearningBusy}
        />
      </div>
    </div>
  );
}

function wbcSetOpenPageContextMenu(context, menu) {
  context.pageContextMenuRef.current = menu;
  context.setPageContextMenu(menu);
}

function wbcClearPendingPageContextMenu(context) {
  context.pendingPageContextMenuRef.current = null;
  if (context.pageContextPreviewTimerRef.current) {
    clearTimeout(context.pageContextPreviewTimerRef.current);
    context.pageContextPreviewTimerRef.current = null;
  }
}

function wbcClosePageContextMenu(context) {
  var current = context.pageContextMenuRef.current;
  var pending = context.pendingPageContextMenuRef.current;
  wbcClearPendingPageContextMenu(context);
  wbcSetOpenPageContextMenu(context, null);
  if ((current && current.browserPreview) || pending) {
    wbcNotifyBrowserWindowInteraction(
      false, "context-menu",
      (current && current.browserSessionId) || (pending && pending.browserSessionId)
        || context.activeChatIdRef.current
    );
  }
}

function wbcOpenPageContextMenu(context, event) {
  var chat = context.activeChat;
  if (!chat || chat.legacy || !wbcCanOpenPageContextMenu(event)) return;
  event.preventDefault();
  event.stopPropagation();
  wbcClosePageContextMenu(context);
  var nativeHost = document.querySelector(".wbc-browser-window .browser-native-host");
  var nativeRect = nativeHost && nativeHost.getBoundingClientRect();
  var placement = wbcPageContextMenuPlacement(event.clientX, event.clientY, nativeRect);
  var menu = {
    left: placement.left, top: placement.top, browserPreview: false,
    browserSessionId: String(chat.id || ""),
  };
  if (!placement.overlapsBrowser) {
    wbcSetOpenPageContextMenu(context, menu);
    return;
  }
  context.pendingPageContextMenuRef.current = menu;
  wbcNotifyBrowserWindowInteraction(true, "context-menu", menu.browserSessionId);
  context.pageContextPreviewTimerRef.current = setTimeout(function () {
    if (context.pendingPageContextMenuRef.current !== menu) return;
    wbcClearPendingPageContextMenu(context);
    wbcNotifyBrowserWindowInteraction(false, "context-menu", menu.browserSessionId);
    workbenchServices.feedback().showToast(
      wbcT("workbenchChat.contextMenuUnavailable", "Could not open the chat menu over the browser window."),
      "warning"
    );
  }, 900);
}

export {
  WbcChatPageContextMenu,
  wbcClearPendingPageContextMenu,
  wbcClosePageContextMenu,
  wbcOpenPageContextMenu,
  wbcSetOpenPageContextMenu,
}
