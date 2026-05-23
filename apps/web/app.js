const state = {
  sessionId: null,
  busy: false,
  listening: false,
  spaceDown: false,
  providerConfigured: false,
  recognition: null,
  locale: navigator.language && navigator.language.toLowerCase().startsWith("en") ? "en-GB" : "es-ES",
}

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition

const statusLine = document.querySelector("#status-line")
const talkButton = document.querySelector("#talk-button")
const spokenText = document.querySelector("#spoken-text")
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
  spokenText.textContent = ""
  refreshButton()
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

  await ensureSession()
  cancelSpeaking()
  state.busy = true

  logDebug("User", transcript)
  setAvatarState("thinking", "curious", "Thinking...")
  spokenText.textContent = ""
  talkButton.textContent = "Interrupt"

  try {
    const response = await fetch("/api/turn/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
            setAvatarState("speaking", "friendly", "Speaking...")
          }
        } else if (data.type === "done") {
          done = true
          state.busy = false
          state.sessionId = data.session_id
          logDebug("Chirplet", fullText)
          speakResponse(fullText, data.voice_locale || state.locale)
        } else if (data.type === "error") {
          done = true
          state.busy = false
          spokenText.textContent = data.text
          setAvatarState("error", "concerned", data.text)
          state.sessionId = data.session_id
        }
      }
    }
  } catch (error) {
    state.busy = false
    if (error.name === "AbortError") return
    logDebug("Error", error.message)
    setAvatarState("error", "concerned", "I could not reach the LLM provider.")
  }
}

function speakResponse(text, lang) {
  if (!window.speechSynthesis) {
    setAvatarState("idle", "neutral", text)
    return
  }

  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = lang || state.locale
  utterance.rate = 1
  utterance.pitch = 1
  utterance.volume = 1

  utterance.onstart = () => {
    setAvatarState("speaking", "friendly", "Speaking...")
  }

  utterance.onend = () => {
    setAvatarState("idle", "neutral", "Ready.")
  }

  utterance.onerror = () => {
    setAvatarState("idle", "neutral", "Ready.")
  }

  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(utterance)
}

function startListening() {
  if (!SpeechRecognition) {
    setAvatarState("error", "concerned", "Browser does not support speech recognition. Use the debug panel.")
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

function handleTalkStart() {
  if (!state.providerConfigured) {
    setAvatarState("disconnected", "neutral", "Configure an LLM provider in .env to begin.")
    return
  }

  if (state.busy) {
    cancelSpeaking()
    state.busy = false
    setAvatarState("idle", "neutral", "Ready.")
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

async function boot() {
  try {
    const response = await fetch("/api/health")
    if (!response.ok) throw new Error(`Health check failed with ${response.status}`)
    const health = await response.json()
    state.providerConfigured = health.provider_configured
    refreshButton()

    if (!state.providerConfigured) {
      setAvatarState("disconnected", "neutral", "Configure an LLM provider and restart the app.")
      return
    }

    await ensureSession()
    setAvatarState("idle", "neutral", "Hold spacebar or press Talk to begin.")
  } catch (error) {
    logDebug("Boot", error.message)
    setAvatarState("error", "concerned", "I could not start the app.")
  }
}

window.addEventListener("load", boot)
