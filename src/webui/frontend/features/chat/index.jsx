import { WORKBENCH_BUDGET_CODES, WorkbenchChatModel } from "../../workbench-chat.jsx"
import { WorkbenchChatRuntimes } from "./file-resources.jsx"
import { WbcComposer, wbcClearComposerDraft } from "./composer.jsx"
import {
  WbcAgentNotification,
  WbcAssistantMessage,
  WbcLiveMessage,
  WbcQuestionPrompt,
  WbcRuntimeTranscript,
  WbcUserMessage,
} from "./messages.jsx"
import { WbcThreadItem } from "./conversation.jsx"
import { WbcProjectRail, WbcRail } from "./rail.jsx"
import { WorkbenchChatPage } from "./page.jsx"
import { WbcDetachedPaneApp } from "./context-panel.jsx"

/** Public Chat feature contract used by Workbench and Quick Chat. */
const chatService = {
  Model: WorkbenchChatModel,
  Runtimes: WorkbenchChatRuntimes,
  budgetCodes: WORKBENCH_BUDGET_CODES,
  Composer: WbcComposer,
  UserMessage: WbcUserMessage,
  AssistantMessage: WbcAssistantMessage,
  AgentNotification: WbcAgentNotification,
  QuestionPrompt: WbcQuestionPrompt,
  ThreadItem: WbcThreadItem,
  LiveMessage: WbcLiveMessage,
  RuntimeTranscript: WbcRuntimeTranscript,
  clearComposerDraft: wbcClearComposerDraft,
  DetachedPaneApp: WbcDetachedPaneApp,
  Rail: WbcProjectRail,
  RailView: WbcRail,
  Page: WorkbenchChatPage,
}

window.CyreneUI.chat = window.CyreneUI.register("chat", chatService)

export { chatService }
