const state = {
  sessionId: null,
  busy: false,
  listening: false,
  spaceDown: false,
  providerConfigured: false,
  recognition: null,
  turnController: null,
  locale: navigator.language && navigator.language.toLowerCase().startsWith("en") ? "en-GB" : "es-ES",
}

let turnCounter = 0
function nextRequestId() {
  turnCounter += 1
  return `web-${Date.now().toString(36)}-${turnCounter}`
}

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition

const statusLine = document.querySelector("#status-line")
const talkButton = document.querySelector("#talk-button")
const spokenText = document.querySelector("#spoken-text")
const manualInput = document.querySelector("#manual-input")
const manualSend = document.querySelector("#manual-send")
const voiceSelect = document.querySelector("#voice-select")
const sessionsList = document.querySelector("#sessions-list")
const sessionsRefresh = document.querySelector("#sessions-refresh")
const sessionsNew = document.querySelector("#sessions-new")
const sessionsTurns = document.querySelector("#sessions-turns")
const debugLog = document.querySelector("#debug-log")

const VOICE_STORAGE_KEY = "chirplet.voiceURI"

function logDebug(label, value) {
  const timestamp = new Date().toLocaleTimeString()
  debugLog.textContent = `[${timestamp}] ${label}: ${value}\n${debugLog.textContent}`.trim()
}

function setAvatarState(nextState, mood, message, options = {}) {
  const { clearText = false, mouth, action } = options

  document.body.dataset.state = nextState
  if (mood) {
    document.body.dataset.mood = mood
  }
  if (mouth) {
    document.body.dataset.mouth = mouth
  }
  if (action) {
    document.body.dataset.action = action
  }
  if (message) {
    statusLine.textContent = message
  }
  if (clearText) {
    spokenText.textContent = ""
  }
  refreshButton()
}

function applyExpression(expression, message, options = {}) {
  const nextExpression = expression || {}

  setAvatarState(
    nextExpression.state || "idle",
    nextExpression.mood || "neutral",
    message,
    {
      ...options,
      mouth: nextExpression.mouth || options.mouth || "closed",
    },
  )
}

function refreshButton() {
  if (!state.providerConfigured) {
    talkButton.disabled = true
    talkButton.textContent = "No provider"
    return
  }

  talkButton.disabled = false

  if (state.listening) {
    talkButton.textContent = "Listening..."
    return
  }

  if (state.busy) {
    talkButton.textContent = "Interrupt"
    return
  }

  talkButton.textContent = "Talk"
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

function interruptTurn() {
  if (state.turnController) {
    state.turnController.abort()
    state.turnController = null
  }

  cancelSpeaking()
  state.busy = false
  setAvatarState("idle", "neutral", "Interrupted.", {
    mouth: "closed",
    action: "idle",
  })
}

async function ensureSession() {
  if (state.sessionId) {
    return state.sessionId
  }

  try {
    const response = await fetch("/api/session", { method: "POST" })
    if (!response.ok) throw new Error(`Request failed with ${response.status}`)
    const payload = await response.json()
    state.sessionId = payload.session_id
  } catch (e) {
    logDebug("Session", e.message)
  }
  return state.sessionId
}

async function submitTurn(transcript) {
  if (!transcript) return
  transcript = transcript.trim().slice(0, 500)

  await ensureSession()
  cancelSpeaking()
  state.busy = true

  logDebug("User", transcript)
  setAvatarState("thinking", "curious", "Thinking...", {
    clearText: true,
    mouth: "closed",
    action: "idle",
  })
  talkButton.textContent = "Interrupt"
  const turnController = new AbortController()
  state.turnController = turnController

  try {
    const response = await fetch("/api/turn/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: turnController.signal,
      body: JSON.stringify({
        session_id: state.sessionId,
        transcript,
        locale: state.locale,
      }),
    })

    if (!response.ok) throw new Error(`Stream failed with ${response.status}`)

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    let done = false
    let fullText = ""

    while (!done) {
      const { value, done: readerDone } = await reader.read()
      if (readerDone) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split("\n")
      buffer = lines.pop() || ""

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue
        const data = JSON.parse(line.slice(6))

        if (data.type === "token") {
          fullText += data.text
          spokenText.textContent = fullText
          if (document.body.dataset.state !== "speaking") {
            setAvatarState("speaking", "friendly", "Speaking...", {
              mouth: "open",
              action: "idle",
            })
          }
        } else if (data.type === "done") {
          done = true
          state.busy = false
          state.sessionId = data.session_id
          fullText = data.text || fullText
          spokenText.textContent = fullText
          logDebug("Chirplet", fullText)
          speakResponse(fullText, data.voice_locale || state.locale, data.expression, data.action)
        } else if (data.type === "error") {
          done = true
          state.busy = false
          spokenText.textContent = data.text
          if (data.issue) {
            logDebug("Provider", data.issue)
          }
          setAvatarState("error", "concerned", data.text, {
            mouth: "closed",
            action: "idle",
          })
          state.sessionId = data.session_id
        }
      }
    }
  } catch (error) {
    state.busy = false
    if (error.name === "AbortError") return
    logDebug("Error", error.message)
    setAvatarState("error", "concerned", "I could not reach the LLM provider.", {
      mouth: "closed",
      action: "idle",
    })
  } finally {
    if (state.turnController === turnController) {
      state.turnController = null
    }
  }
}

function populateVoiceList() {
  if (!window.speechSynthesis) return
  const voices = window.speechSynthesis.getVoices()
  if (voices.length === 0) return

  const savedURI = localStorage.getItem(VOICE_STORAGE_KEY)
  const current = savedURI ? voices.find((v) => v.voiceURI === savedURI) : null

  const matching = voices.filter((v) =>
    v.lang && v.lang.toLowerCase().startsWith(state.locale.toLowerCase().split("-")[0])
  )
  const list = matching.length > 0 ? matching : voices

  voiceSelect.innerHTML = ""
  const defaultOption = document.createElement("option")
  defaultOption.value = ""
  defaultOption.textContent = current
    ? `(browser default — ${current.name})`
    : "(browser default)"
  voiceSelect.appendChild(defaultOption)

  for (const voice of list) {
    const option = document.createElement("option")
    option.value = voice.voiceURI
    option.textContent = `${voice.name} (${voice.lang})${voice.default ? " — default" : ""}`
    voiceSelect.appendChild(option)
  }

  voiceSelect.disabled = false
  voiceSelect.value = current ? current.voiceURI : ""
}

function speakResponse(text, lang, expression, action) {
  if (!window.speechSynthesis) {
    spokenText.textContent = text
    applyExpression(expression, text, { action: action || "idle" })
    return
  }

  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = lang || state.locale
  utterance.rate = 1
  utterance.pitch = 1
  utterance.volume = 1

  const savedURI = localStorage.getItem(VOICE_STORAGE_KEY)
  if (savedURI) {
    const voice = window.speechSynthesis.getVoices().find((v) => v.voiceURI === savedURI)
    if (voice) {
      utterance.voice = voice
    }
  }

  utterance.onstart = () => {
    applyExpression({
      state: "speaking",
      mood: expression?.mood || "friendly",
      mouth: expression?.mouth || "open",
    }, "Speaking...", { action: action || "idle" })
  }

  utterance.onend = () => {
    setAvatarState("idle", "neutral", "Ready.", {
      mouth: "closed",
      action: "idle",
    })
  }

  utterance.onerror = () => {
    setAvatarState("idle", "neutral", "Ready.", {
      mouth: "closed",
      action: "idle",
    })
  }

  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(utterance)
}

function startListening() {
  if (!SpeechRecognition) {
    setAvatarState("error", "concerned", "Browser does not support speech recognition. Use the debug panel.", {
      mouth: "closed",
      action: "idle",
    })
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
    setAvatarState("listening", "curious", "Listening...", {
      mouth: "closed",
      action: "idle",
    })
  }

  recognition.onerror = (event) => {
    state.listening = false
    state.recognition = null
    setAvatarState("error", "concerned", `Microphone error: ${event.error}`, {
      mouth: "closed",
      action: "idle",
    })
  }

  recognition.onresult = async (event) => {
    const transcript = event.results[0]?.[0]?.transcript?.trim()
    state.listening = false
    state.recognition = null
    if (!transcript) {
      setAvatarState("idle", "neutral", "I did not detect speech.", {
        mouth: "closed",
        action: "idle",
      })
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

function handleTalkStart() {
  if (!state.providerConfigured) {
    setAvatarState("disconnected", "neutral", "Configure an LLM provider in .env to begin.", {
      mouth: "closed",
      action: "idle",
    })
    return
  }

  if (state.busy) {
    interruptTurn()
    return
  }

  startListening()
}

function handleTalkStop() {
  if (state.listening) {
    stopListening()
  }
}

talkButton.addEventListener("mousedown", (e) => {
  e.preventDefault()
  handleTalkStart()
})

talkButton.addEventListener("mouseup", () => {
  handleTalkStop()
})

talkButton.addEventListener("mouseleave", () => {
  handleTalkStop()
})

talkButton.addEventListener("touchstart", (e) => {
  e.preventDefault()
  handleTalkStart()
})

talkButton.addEventListener("touchend", () => {
  handleTalkStop()
})

document.addEventListener("keydown", (e) => {
  if (e.key === " " && !e.repeat && document.activeElement !== manualInput) {
    e.preventDefault()
    state.spaceDown = true
    handleTalkStart()
  }
})

document.addEventListener("keyup", (e) => {
  if (e.key === " " && state.spaceDown) {
    e.preventDefault()
    state.spaceDown = false
    handleTalkStop()
  }
})

manualSend.addEventListener("click", async () => {
  const transcript = manualInput.value.trim()
  if (!transcript) return
  manualInput.value = ""
  await submitTurn(transcript)
})

manualInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault()
    manualSend.click()
  }
})

voiceSelect.addEventListener("change", () => {
  if (voiceSelect.value) {
    localStorage.setItem(VOICE_STORAGE_KEY, voiceSelect.value)
    logDebug("Voice", voiceSelect.value)
  } else {
    localStorage.removeItem(VOICE_STORAGE_KEY)
    logDebug("Voice", "browser default")
  }
})

async function loadSessions() {
  try {
    const res = await fetch("/api/sessions")
    if (!res.ok) throw new Error(`Request failed with ${res.status}`)
    const payload = await res.json()
    const sessions = payload.sessions || []
    sessionsList.innerHTML = ""
    if (sessions.length === 0) {
      const option = document.createElement("option")
      option.value = ""
      option.textContent = "(no sessions yet)"
      option.disabled = true
      option.selected = true
      sessionsList.appendChild(option)
      sessionsTurns.textContent = ""
      return
    }
    for (const s of sessions) {
      const option = document.createElement("option")
      option.value = s.session_id
      const ts = (s.last_active_at || "").slice(0, 19).replace("T", " ")
      option.textContent = `${s.session_id.slice(0, 8)}  ·  ${s.turn_count} turn${s.turn_count === 1 ? "" : "s"}  ·  ${ts}`
      sessionsList.appendChild(option)
    }
    // Auto-select the first (most recent) session and load its turns.
    sessionsList.selectedIndex = 0
    await loadSessionTurns(sessions[0].session_id)
  } catch (e) {
    logDebug("Sessions", e.message)
  }
}

async function loadSessionTurns(sessionId) {
  if (!sessionId) {
    sessionsTurns.textContent = ""
    return
  }
  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/turns`)
    if (!res.ok) {
      sessionsTurns.textContent = `(failed to load: ${res.status})`
      return
    }
    const payload = await res.json()
    const turns = payload.turns || []
    if (turns.length === 0) {
      sessionsTurns.textContent = "(no turns yet)"
      return
    }
    sessionsTurns.textContent = turns
      .map((t) => `[${(t.created_at || "").slice(0, 19)}]\n  you: ${t.user}\n  chirplet: ${t.assistant}`)
      .join("\n\n")
  } catch (e) {
    sessionsTurns.textContent = `(error: ${e.message})`
  }
}

sessionsRefresh.addEventListener("click", loadSessions)
sessionsList.addEventListener("change", () => {
  loadSessionTurns(sessionsList.value)
})

async function createNewSession() {
  try {
    const res = await fetch("/api/session", { method: "POST" })
    if (!res.ok) throw new Error(`Request failed with ${res.status}`)
    const payload = await res.json()
    logDebug("Session", `created ${payload.session_id.slice(0, 8)}`)
    await loadSessions()
  } catch (e) {
    logDebug("Session", e.message)
  }
}

sessionsNew.addEventListener("click", createNewSession)

if (window.speechSynthesis) {
  window.speechSynthesis.addEventListener("voiceschanged", populateVoiceList)
}

async function boot() {
  try {
    const response = await fetch("/api/health")
    if (!response.ok) throw new Error(`Health check failed with ${response.status}`)
    const health = await response.json()
    state.providerConfigured = health.provider_configured
    refreshButton()

    if (!state.providerConfigured) {
      setAvatarState("disconnected", "neutral", "Configure an LLM provider and restart the app.", {
        mouth: "closed",
        action: "idle",
      })
      return
    }

    await ensureSession()
    setAvatarState("idle", "neutral", "Hold spacebar or press Talk to begin.", {
      mouth: "closed",
      action: "idle",
    })
    populateVoiceList()
    loadSessions()
  } catch (error) {
    logDebug("Boot", error.message)
    setAvatarState("error", "concerned", "I could not start the app.", {
      mouth: "closed",
      action: "idle",
    })
  }
}

window.addEventListener("load", boot)
