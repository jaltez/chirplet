const state = {
  sessionId: null,
  busy: false,
  listening: false,
  hermesConfigured: false,
  recognition: null,
  pendingRequest: null,
  locale: navigator.language && navigator.language.toLowerCase().startsWith("en") ? "en-GB" : "es-ES",
}

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition

const statusLine = document.querySelector("#status-line")
const talkButton = document.querySelector("#talk-button")
const manualInput = document.querySelector("#manual-input")
const manualSend = document.querySelector("#manual-send")
const debugLog = document.querySelector("#debug-log")

function logDebug(label, value) {
  const timestamp = new Date().toLocaleTimeString()
  debugLog.textContent = `[${timestamp}] ${label}: ${value}\n${debugLog.textContent}`.trim()
}

function setAvatarState(nextState, mood, message) {
  document.body.dataset.state = nextState
  if (mood) {
    document.body.dataset.mood = mood
  }
  if (message) {
    statusLine.textContent = message
  }
  refreshButton()
}

function refreshButton() {
  if (!state.hermesConfigured) {
    talkButton.disabled = true
    talkButton.textContent = "Configure Hermes"
    return
  }

  talkButton.disabled = false

  if (state.listening) {
    talkButton.textContent = "Stop"
    return
  }

  if (state.busy) {
    talkButton.textContent = "Interrupt"
    return
  }

  talkButton.textContent = "Talk"
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options)
  if (!response.ok) {
    throw new Error(`Request failed with ${response.status}`)
  }
  return response.json()
}

async function ensureSession() {
  if (state.sessionId) {
    return state.sessionId
  }

  const payload = await fetchJson("/api/session", { method: "POST" })
  state.sessionId = payload.session_id
  return state.sessionId
}

function stopListening() {
  if (state.recognition) {
    state.recognition.onend = null
    state.recognition.stop()
    state.recognition = null
  }
  state.listening = false
  if (!state.busy) {
    setAvatarState("idle", "neutral", "Ready.")
  } else {
    refreshButton()
  }
}

function cancelSpeaking() {
  if (window.speechSynthesis && window.speechSynthesis.speaking) {
    window.speechSynthesis.cancel()
  }
}

function abortPendingTurn() {
  if (state.pendingRequest) {
    state.pendingRequest.abort()
    state.pendingRequest = null
  }
}

async function submitTurn(transcript) {
  if (!transcript) {
    return
  }

  await ensureSession()
  cancelSpeaking()
  abortPendingTurn()

  const controller = new AbortController()
  state.pendingRequest = controller
  state.busy = true

  logDebug("User", transcript)
  setAvatarState("thinking", "curious", "Thinking...")

  try {
    const payload = await fetchJson("/api/turn", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        session_id: state.sessionId,
        transcript,
        locale: state.locale,
      }),
      signal: controller.signal,
    })

    state.sessionId = payload.session_id
    logDebug("Chirplet", payload.assistant.text)

    const mood = payload.assistant.expression?.mood || "friendly"
    const speechText = payload.assistant.text

    if (!window.speechSynthesis) {
      state.busy = false
      state.pendingRequest = null
      setAvatarState(payload.meta.fallback_used ? "error" : "idle", mood, speechText)
      return
    }

    const utterance = new SpeechSynthesisUtterance(speechText)
    utterance.lang = payload.assistant.voice_locale || state.locale
    utterance.rate = 1
    utterance.pitch = 1

    utterance.onstart = () => {
      setAvatarState(payload.meta.fallback_used ? "error" : "speaking", mood, payload.meta.fallback_used ? "Fallback response." : "Speaking...")
    }

    utterance.onend = () => {
      state.busy = false
      state.pendingRequest = null
      setAvatarState("idle", "neutral", "Ready.")
    }

    utterance.onerror = () => {
      state.busy = false
      state.pendingRequest = null
      setAvatarState("error", "concerned", "I could not play the voice response.")
    }

    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utterance)
  } catch (error) {
    state.busy = false
    state.pendingRequest = null

    if (error.name === "AbortError") {
      setAvatarState("idle", "neutral", "Solicitud cancelada.")
      return
    }

    logDebug("Error", error.message)
    setAvatarState("error", "concerned", "I could not connect to Hermes.")
  }
}

function startListening() {
  if (!SpeechRecognition) {
    setAvatarState("error", "concerned", "Your browser does not support speech recognition. Use the debug panel.")
    return
  }

  cancelSpeaking()

  const recognition = new SpeechRecognition()
  recognition.lang = state.locale
  recognition.interimResults = false
  recognition.maxAlternatives = 1

  recognition.onstart = () => {
    state.recognition = recognition
    state.listening = true
    setAvatarState("listening", "curious", "Listening...")
  }

  recognition.onerror = (event) => {
    state.listening = false
    state.recognition = null
    setAvatarState("error", "concerned", `Microphone error: ${event.error}`)
  }

  recognition.onresult = async (event) => {
    const transcript = event.results[0]?.[0]?.transcript?.trim()
    state.listening = false
    state.recognition = null
    if (!transcript) {
      setAvatarState("idle", "neutral", "I did not detect speech.")
      return
    }
    await submitTurn(transcript)
  }

  recognition.onend = () => {
    if (state.recognition === recognition) {
      state.recognition = null
      state.listening = false
      if (!state.busy) {
        setAvatarState("idle", "neutral", "Ready.")
      }
    }
  }

  recognition.start()
}

function handleTalkButton() {
  if (!state.hermesConfigured) {
    setAvatarState("disconnected", "neutral", "Configure Hermes in .env to begin.")
    return
  }

  if (state.listening) {
    stopListening()
    return
  }

  if (state.busy) {
    cancelSpeaking()
    abortPendingTurn()
    state.busy = false
  }

  startListening()
}

async function boot() {
  try {
    const health = await fetchJson("/api/health")
    state.hermesConfigured = health.hermes_configured
    refreshButton()

    if (!state.hermesConfigured) {
      setAvatarState("disconnected", "neutral", "Configure Hermes and restart the app.")
      return
    }

    await ensureSession()
    setAvatarState("idle", "neutral", "Press talk to begin.")
  } catch (error) {
    logDebug("Boot", error.message)
    setAvatarState("error", "concerned", "I could not start the app.")
  }
}

talkButton.addEventListener("click", handleTalkButton)

manualSend.addEventListener("click", async () => {
  const transcript = manualInput.value.trim()
  if (!transcript) {
    return
  }
  manualInput.value = ""
  await submitTurn(transcript)
})

manualInput.addEventListener("keydown", async (event) => {
  if (event.key === "Enter") {
    event.preventDefault()
    manualSend.click()
  }
})

window.addEventListener("load", boot)
