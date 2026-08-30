import { workbenchServices } from "../../shared/runtime/services.jsx"
import { PlanTimeline } from "./plan-timeline.jsx"

var { useEffect, useRef, useState } = React

function planSteps(plan) {
  return Array.isArray(plan && plan.steps) ? plan.steps : []
}

function usePlanFilePolling(chatId, planRef, savingRef, applyPlan) {
  useEffect(function () {
    if (!chatId) return undefined
    var disposed = false
    var timer = 0
    function schedule() {
      if (!disposed) timer = window.setTimeout(refresh, 1200)
    }
    function refresh() {
      if (disposed) return
      if (savingRef.current) { schedule(); return }
      fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/plan", { cache: "no-store" })
        .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error(String(response.status))) })
        .then(function (payload) {
          var latest = payload && payload.plan
          if (!latest || JSON.stringify(latest) === JSON.stringify(planRef.current)) return
          applyPlan(latest, true)
        })
        .catch(function () {})
        .finally(schedule)
    }
    schedule()
    return function () {
      disposed = true
      if (timer) window.clearTimeout(timer)
    }
  }, [chatId])
}

function useConversationPlanState(chatId, projectedPlan) {
  var [plan, setPlan] = useState(projectedPlan || { steps: [] })
  var [saving, setSaving] = useState(false)
  var planRef = useRef(projectedPlan || { steps: [] })
  var savingRef = useRef(false)

  function applyPlan(updated, broadcast) {
    if (!updated || typeof updated !== "object") return
    planRef.current = updated
    setPlan(updated)
    if (broadcast) {
      window.dispatchEvent(new CustomEvent("cyrene:conversation-plan-updated", {
        detail: { chatId: chatId, plan: updated },
      }))
    }
  }

  useEffect(function () {
    applyPlan(projectedPlan || { steps: [] }, false)
  }, [projectedPlan])

  usePlanFilePolling(chatId, planRef, savingRef, applyPlan)

  function persist(nextPlan) {
    if (!chatId || saving) return Promise.resolve(false)
    setSaving(true)
    savingRef.current = true
    return fetch("/api/workbench/chats/" + encodeURIComponent(chatId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activePlan: nextPlan }),
    }).then(function (response) {
      return response.json().catch(function () { return {} }).then(function (payload) {
        if (!response.ok) throw new Error(String(payload.message || payload.detail || payload.error || response.status))
        var updated = payload && payload.chat && payload.chat.activePlan || nextPlan
        applyPlan(updated, true)
        return true
      })
    }).catch(function (error) {
      workbenchServices.feedback().showToast(error && error.message || String(error), "error")
      return false
    }).finally(function () {
      savingRef.current = false
      setSaving(false)
    })
  }

  var controller = chatId ? {
    busy: saving,
    patchStep: function (stepId, patch) {
      return persist(Object.assign({}, plan, { steps: planSteps(plan).map(function (step) {
        return String(step && step.id || "") === String(stepId || "") ? Object.assign({}, step, patch) : step
      }) }))
    },
    deleteStep: function (stepId) {
      var remaining = planSteps(plan).filter(function (step) { return String(step && step.id || "") !== String(stepId || "") })
      return persist(Object.assign({}, plan, { steps: remaining.map(function (step) {
        return Object.assign({}, step, { dependsOn: (step.dependsOn || []).filter(function (id) { return String(id) !== String(stepId) }) })
      }) }))
    },
    addStep: function (step) {
      var used = new Set(planSteps(plan).map(function (item) { return String(item && item.id || "") }))
      var index = planSteps(plan).length + 1
      var id = "step_" + index
      while (used.has(id)) { index += 1; id = "step_" + index }
      return persist(Object.assign({}, plan, { steps: planSteps(plan).concat([Object.assign({ id: id, status: "pending", note: "" }, step)]) }))
    },
    reorderSteps: function (orderedIds) {
      var byId = new Map(planSteps(plan).map(function (step) { return [String(step && step.id || ""), step] }))
      return persist(Object.assign({}, plan, { steps: orderedIds.map(function (id) { return byId.get(String(id)) }).filter(Boolean) }))
    },
  } : null

  return { plan: plan, saving: saving, controller: controller }
}

function ConversationPlanTimeline({ chatId, projectId, plan, className }) {
  var state = useConversationPlanState(String(chatId || ""), plan)
  function checkWorkspacePath(path) {
    if (!projectId) return Promise.resolve({ exists: false, path: path, error: "Project workspace is unavailable." })
    return fetch("/api/projects/" + encodeURIComponent(projectId) + "/files/exists?path=" + encodeURIComponent(path || ""), { cache: "no-store" })
      .then(function (response) { return response.json().then(function (payload) {
        if (!response.ok) throw new Error(String(payload.message || payload.error || response.status))
        return payload
      }) })
  }
  return <div className={className || ""}>
    <PlanTimeline
      plan={state.plan}
      hostStatus={state.plan && state.plan.status}
      controller={state.controller}
      busy={state.saving}
      checkWorkspacePath={checkWorkspacePath}
    />
  </div>
}

export { ConversationPlanTimeline }
