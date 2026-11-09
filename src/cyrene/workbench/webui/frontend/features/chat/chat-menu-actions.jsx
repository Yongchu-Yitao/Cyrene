import { WBC_ICONS, wbcT } from "../../workbench-chat.jsx"

export function WbcChatMenuActions({ chat, isPinned, onClose, onTogglePinned, onRename, onDelete }) {
  return <React.Fragment>
                  <button type="button" role="menuitem" data-cyrene-node-id="chat_menu_pin" className="wbc-chat-pin-action" onClick={function (e) {
                    e.stopPropagation();
                    onClose();
                    if (onTogglePinned) onTogglePinned(chat, !isPinned);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.pin}</span>
                    <span>{isPinned
                      ? wbcT("workbenchChat.unpin", "Unpin chat")
                      : wbcT("workbenchChat.pin", "Pin chat")}</span>
                  </button>
                  <button type="button" role="menuitem" data-cyrene-node-id="chat_menu_rename" className="wbc-chat-menu-action" onClick={function (e) {
                    e.stopPropagation();
                    onClose();
                    onRename(chat);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span>
                    <span>{wbcT("workbenchChat.rename", "Rename chat")}</span>
                  </button>
                  <button type="button" role="menuitem" data-cyrene-node-id="chat_menu_delete" className="wbc-chat-menu-action danger" onClick={function (e) {
                    e.stopPropagation();
                    onClose();
                    onDelete && onDelete(chat.id);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.trash}</span>
                    <span>{wbcT("workbenchChat.delete", "Delete chat")}</span>
                  </button>
  </React.Fragment>;
}
