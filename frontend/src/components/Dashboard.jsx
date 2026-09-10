import { useCallback, useRef, useState } from "react";
import VoiceOrb from "./VoiceOrb";
import { sendChat } from "../services/api";
import { useAgentSocket } from "../hooks/useAgentSocket";
import { useSpeechToText } from "../hooks/useSpeechToText";
import { useTextToSpeech } from "../hooks/useTextToSpeech";

const MAX_LOG_ENTRIES = 30;

function labelForEvent(evt) {
  switch (evt.type) {
    case "agent_thinking":
      return "reasoning";
    case "capability_started":
      return `${evt.capability} running${evt.detail ? ` — ${evt.detail}` : ""}`;
    case "capability_finished":
      return `${evt.capability} done`;
    case "error":
      return evt.message || "error";
    default:
      return evt.type;
  }
}

function labelForMicError(code) {
  switch (code) {
    case "not-allowed":
    case "permission-denied":
      return "Mic permission denied — allow it in browser settings.";
    case "no-speech":
      return "Didn't catch that — try again.";
    case "audio-capture":
      return "No microphone found.";
    default:
      return "Voice input error — try again.";
  }
}

export default function Dashboard({ sessionId }) {
  const [orbState, setOrbState] = useState("idle");
  const [orbDetail, setOrbDetail] = useState("");
  const [inputValue, setInputValue] = useState("");
  const [response, setResponse] = useState(null);
  const [error, setError] = useState(null);
  const [log, setLog] = useState([]);
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const speakingTimer = useRef(null);

  const handleEvent = useCallback((evt) => {
    const entry = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      time: new Date().toLocaleTimeString([], { hour12: false }),
      type: evt.type,
      label: labelForEvent(evt),
    };
    setLog((prev) => [entry, ...prev].slice(0, MAX_LOG_ENTRIES));

    if (evt.type === "capability_started" || evt.type === "capability_finished") {
      setOrbDetail(evt.type === "capability_started" ? entry.label : "");
    } else if (evt.type === "agent_thinking") {
      setOrbDetail("");
    }
  }, []);

  const { connected } = useAgentSocket(sessionId, handleEvent);

  // Voice input: fills the text box with recognized speech — never
  // auto-sends, so the user can review/edit before submitting.
  const handleFinalTranscript = useCallback((text) => {
    if (!text) return;
    setInputValue((prev) => (prev ? `${prev} ${text}` : text));
  }, []);

  const {
    isSupported: micSupported,
    isListening,
    interimText,
    error: micError,
    toggle: toggleMic,
  } = useSpeechToText({ onFinalResult: handleFinalTranscript });

  const { isSupported: ttsSupported, isSpeaking, speak, stop: stopSpeaking } = useTextToSpeech();

  function toggleVoiceOutput() {
    setVoiceEnabled((prev) => {
      const next = !prev;
      if (!next) stopSpeaking();
      return next;
    });
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const message = inputValue.trim();
    if (!message || orbState === "thinking") return;

    clearTimeout(speakingTimer.current);
    stopSpeaking(); // interrupt any reply still being read aloud
    setError(null);
    setOrbDetail("");
    setOrbState("thinking");
    setInputValue("");

    try {
      const data = await sendChat(message, sessionId);
      setResponse(data);
      setOrbState("speaking");
      setOrbDetail("");

      if (voiceEnabled && ttsSupported) {
        speak(data.response, { onEnd: () => setOrbState("idle") });
      } else {
        speakingTimer.current = setTimeout(() => setOrbState("idle"), 1600);
      }
    } catch (err) {
      setError(err.message || "Request failed.");
      setOrbState("error");
      setOrbDetail("");
      speakingTimer.current = setTimeout(() => setOrbState("idle"), 1600);
    }
  }

  return (
    <div className="dashboard">
      <div className="dashboard-orb-wrap">
        <VoiceOrb state={orbState} detail={orbDetail} />
        <span className={`ws-indicator ${connected ? "ws-connected" : "ws-disconnected"}`}>
          {connected ? "● live" : "○ offline"}
        </span>

        {ttsSupported && (
          <button
            type="button"
            className={`dashboard-voice-toggle ${voiceEnabled ? "voice-on" : "voice-off"}`}
            onClick={toggleVoiceOutput}
            title={voiceEnabled ? "Voice replies on — click to mute" : "Voice replies muted — click to enable"}
          >
            {voiceEnabled ? "🔊" : "🔇"}
          </button>
        )}
      </div>

      <form className="dashboard-input-row" onSubmit={handleSubmit}>
        <input
          type="text"
          className="dashboard-input"
          placeholder="Ask JESSY something..."
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          disabled={orbState === "thinking"}
        />

        {micSupported && (
          <button
            type="button"
            className={`dashboard-mic-btn ${isListening ? "mic-listening" : ""}`}
            onClick={toggleMic}
            disabled={orbState === "thinking"}
            title={isListening ? "Stop listening" : "Speak your message"}
          >
            {isListening ? "◉" : "🎤"}
          </button>
        )}

        <button
          type="submit"
          className="dashboard-send-btn"
          disabled={orbState === "thinking" || !inputValue.trim()}
        >
          {orbState === "thinking" ? "···" : "Send"}
        </button>
      </form>

      {isListening && (
        <div className="dashboard-mic-status">
          <span className="mic-status-dot" />
          Listening{interimText ? `: "${interimText}"` : "..."}
        </div>
      )}

      {micError && !isListening && (
        <div className="dashboard-mic-error">⚠ {labelForMicError(micError)}</div>
      )}

      {isSpeaking && (
        <div className="dashboard-mic-status">
          <span className="mic-status-dot voice-dot" />
          Speaking...
          <button type="button" className="dashboard-stop-voice-btn" onClick={stopSpeaking}>
            Stop
          </button>
        </div>
      )}

      {(response || error) && (
        <div className={`dashboard-response ${error ? "dashboard-response-error" : ""}`}>
          {error ? (
            <span>⚠ {error}</span>
          ) : (
            <>
              <span className="dashboard-response-meta">
                {response.steps} step{response.steps === 1 ? "" : "s"}
              </span>
              <p>{response.response}</p>
            </>
          )}
        </div>
      )}

      {log.length > 0 && (
        <div className="activity-feed">
          <span className="activity-feed-title">ACTIVITY</span>
          <ul>
            {log.map((entry) => (
              <li key={entry.id} className={`activity-entry activity-${entry.type}`}>
                <span className="activity-time">{entry.time}</span>
                <span className="activity-label">{entry.label}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
