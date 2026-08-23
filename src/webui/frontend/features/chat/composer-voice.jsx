import {
  WbcVoice,
  useWbcEffect,
  useWbcRef,
  useWbcState,
  wbcCreateComposerVoiceFeedback,
  wbcStartVoiceRecorder,
  wbcTranscribeVoiceBlob,
} from "../../workbench-chat.jsx"

function useWbcComposerVoice({
  awaitingAnswer,
  chatId,
  draftRef,
  setDraft,
  setModelOpen,
  setModelPanel,
  setToolsOpen,
  submit,
  textAreaRef,
}) {
  var [voiceSnapshot, setVoiceSnapshot] = useWbcState({ status: {}, activeKey: "" })
  var [voicePhase, setVoicePhase] = useWbcState("")
  var voiceRecorderRef = useWbcRef(null)
  var voiceChatIdRef = useWbcRef(String(chatId || ""))
  var voiceFeedbackRef = useWbcRef(null)
  if (!voiceFeedbackRef.current) voiceFeedbackRef.current = wbcCreateComposerVoiceFeedback()

  useWbcEffect(function () {
    return WbcVoice.subscribe(setVoiceSnapshot)
  }, [])

  useWbcEffect(function () {
    voiceChatIdRef.current = String(chatId || "")
    setVoicePhase("")
    return function () {
      var recorder = voiceRecorderRef.current
      voiceRecorderRef.current = null
      voiceFeedbackRef.current.dismiss()
      if (recorder && typeof recorder.stop === "function") recorder.stop().catch(function () {})
    }
  }, [chatId])

  useWbcEffect(function () {
    if (!awaitingAnswer) return
    setToolsOpen(false)
    setModelOpen(false)
    setModelPanel("root")
    var recorder = voiceRecorderRef.current
    voiceRecorderRef.current = null
    setVoicePhase("")
    voiceFeedbackRef.current.dismiss()
    if (recorder && typeof recorder.stop === "function") recorder.stop().catch(function () {})
  }, [awaitingAnswer])

  function showVoiceError(error) {
    voiceFeedbackRef.current.error(error)
  }

  function transcribeVoiceBlob(blob) {
    return wbcTranscribeVoiceBlob(blob).then(function (transcript) {
      if (transcript === false) {
        voiceFeedbackRef.current.noSpeech()
        return false
      }
      var current = String(draftRef.current || "")
      var combined = current && !/\s$/.test(current) ? current + " " + transcript : current + transcript
      setDraft(combined)
      draftRef.current = combined
      voiceFeedbackRef.current.complete()
      if (voiceSnapshot.status.auto_send_after_asr === true) {
        submit(combined)
        return
      }
      requestAnimationFrame(function () {
        if (textAreaRef.current) textAreaRef.current.focus()
      })
    })
  }

  function finishVoiceInput(recorder) {
    if (!recorder || voiceRecorderRef.current !== recorder) return
    voiceRecorderRef.current = null
    setVoicePhase("transcribing")
    voiceFeedbackRef.current.transcribing()
    recorder.stop()
      .then(transcribeVoiceBlob)
      .catch(showVoiceError)
      .finally(function () { setVoicePhase("") })
  }

  function toggleVoiceInput() {
    if (awaitingAnswer || voicePhase === "starting" || voicePhase === "transcribing") return
    if (voicePhase === "recording") {
      var recorder = voiceRecorderRef.current
      if (!recorder) {
        setVoicePhase("")
        return
      }
      finishVoiceInput(recorder)
      return
    }
    WbcVoice.stop()
    setVoicePhase("starting")
    voiceFeedbackRef.current.starting()
    var startedForChat = String(chatId || "")
    wbcStartVoiceRecorder({
      autoStopOnSilence: voiceSnapshot.status.auto_stop_on_silence !== false,
      onSilence: finishVoiceInput,
    }).then(function (recorder) {
      if (voiceChatIdRef.current !== startedForChat) {
        recorder.stop().catch(function () {})
        return
      }
      voiceRecorderRef.current = recorder
      setVoicePhase("recording")
      voiceFeedbackRef.current.listening()
    }).catch(function (error) {
      setVoicePhase("")
      showVoiceError(error)
    })
  }

  return {
    toggleVoiceInput: toggleVoiceInput,
    voicePhase: voicePhase,
    voiceSnapshot: voiceSnapshot,
  }
}

export { useWbcComposerVoice }
