import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import VoiceOrb from "./VoiceOrb";
import { getHistory, getStatus, sendChat } from "../services/api";
import { useAgentSocket } from "../hooks/useAgentSocket";
import { useSpeechToText } from "../hooks/useSpeechToText";
import { useTextToSpeech } from "../hooks/useTextToSpeech";

const MAX_LOG_ENTRIES = 30;
const CHAT_HISTORY_PAGE = 200;

function labelForEvent(evt) {
  switch (evt.type) {
    case "agent_thinking": return "Reasoning engine active";
    case "capability_started": return `${evt.capability} running${evt.detail ? ` — ${evt.detail}` : ""}`;
    case "capability_finished": return `${evt.capability} completed`;
    case "error": return evt.message || "System error";
    default: return evt.type;
  }
}

function labelForMicError(code) {
  switch (code) {
    case "not-allowed":
    case "permission-denied": return "Mic permission denied — allow it in browser settings.";
    case "no-speech": return "Didn't catch that — try again.";
    case "audio-capture": return "No microphone found.";
    default: return "Voice input error — try again.";
  }
}

function Panel({ title, right, children, className = "" }) {
  return <section className={`hud-panel ${className}`}><div className="panel-head"><span>{title}</span>{right && <span className="panel-right">{right}</span>}</div>{children}</section>;
}

function MiniChart({ variant = 0 }) {
  const points = variant === 1 ? "0,35 18,26 36,30 54,18 72,28 90,10 108,22 126,8 144,17 162,5 180,13" : "0,28 18,20 36,23 54,12 72,18 90,24 108,8 126,19 144,13 162,17 180,6";
  return <svg className="mini-chart" viewBox="0 0 180 45" preserveAspectRatio="none"><polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.6" /><polyline points={points} fill="none" stroke="currentColor" strokeWidth="7" opacity=".05" /></svg>;
}

function formatTime(value) {
  if (!value) return "NOW";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? "NOW" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function commandText(item) {
  return item?.message || item?.text || item?.command || item?.detail || item?.content || item?.input || "Chat received";
}

function normalizeCommand(text) {
  return String(text).trim().replace(/\s+/g, " ").toLowerCase();
}

function findMetric(status, aliases) {
  const aliasTokens = aliases.map((x) => x.toLowerCase().replace(/[^a-z0-9]/g, ""));
  const seen = new WeakSet();
  function walk(value, path = []) {
    if (!value || typeof value !== "object" || seen.has(value)) return null;
    seen.add(value);
    for (const [key, raw] of Object.entries(value)) {
      const pathText = [...path, key].join(" ").toLowerCase().replace(/[^a-z0-9]/g, "");
      const keyText = key.toLowerCase().replace(/[^a-z0-9]/g, "");
      const family = path.join(" ").toLowerCase();
      const readingKey = /(percent|percentage|usage|utilization|load|used)/i.test(key);
      const familyMatch = /(cpu|processor|ram|memory|gpu|graphics|disk|storage)/i.test(family);
      const matches = aliasTokens.some((alias) => pathText.includes(alias) || keyText.includes(alias)) || (familyMatch && readingKey);
      if (matches) {
        if (typeof raw === "number" && Number.isFinite(raw)) return Number(raw.toFixed(1));
        if (typeof raw === "string" && /^\d+(?:\.\d+)?%?$/.test(raw.trim())) return Number(Number(raw.replace("%", "")).toFixed(1));
      }
      if (raw && typeof raw === "object") {
        const nested = walk(raw, [...path, key]);
        if (nested != null) return nested;
      }
    }
    return null;
  }
  return walk(status);
}

const ACTIVE_AGENTS = [
  { name: "Research Agent", state: "ACTIVE", task: "Gathering context", tone: "cyan" },
  { name: "Coding Agent", state: "ACTIVE", task: "Ready for execution", tone: "violet" },
  { name: "Memory Agent", state: "STANDBY", task: "Context synchronized", tone: "green" },
  { name: "Browser Agent", state: "STANDBY", task: "Browser tools ready", tone: "amber" },
];

function Gauge({ name, value, tone = "cyan" }) {
  const safe = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className="system-gauge">
      <div className={`gauge-ring tone-${tone}`} style={{ "--gauge": `${safe * 3.6}deg` }}>
        <div className="gauge-inner"><strong>{value == null ? "N/A" : `${value}%`}</strong></div>
      </div>
      <div className="gauge-copy"><b>{name}</b><span>{value == null ? "No host reading" : "Live host usage"}</span></div>
    </div>
  );
}

async function fetchAllChatHistory() {
  const first = await getHistory({ type: "chat_received", limit: CHAT_HISTORY_PAGE, offset: 0 });
  const total = Number(first.total ?? first.items?.length ?? 0);
  const items = [...(first.items || [])];
  if (items.length >= total) return items;
  const offsets = [];
  for (let offset = items.length; offset < total; offset += CHAT_HISTORY_PAGE) offsets.push(offset);
  const pages = await Promise.all(offsets.map((offset) => getHistory({ type: "chat_received", limit: CHAT_HISTORY_PAGE, offset })));
  pages.forEach((page) => items.push(...(page.items || [])));
  return items;
}

export default function Dashboard({ sessionId, onNavigate }) {
  const [orbState, setOrbState] = useState("idle");
  const [orbDetail, setOrbDetail] = useState("");
  const [inputValue, setInputValue] = useState("");
  const [response, setResponse] = useState(null);
  const [voiceTranscript, setVoiceTranscript] = useState("");
  const [error, setError] = useState(null);
  const [log, setLog] = useState([]);
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const [status, setStatus] = useState(null);
  const [hostMetrics, setHostMetrics] = useState(null);
  const [statusError, setStatusError] = useState(false);
  const [chatHistory, setChatHistory] = useState([]);
  const [historyError, setHistoryError] = useState(null);
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const speakingTimer = useRef(null);
  const responseTimer = useRef(null);
  const previousOrbState = useRef("idle");

  const handleEvent = useCallback((evt) => {
    const entry = { id: `${Date.now()}-${Math.random()}`, time: new Date().toLocaleTimeString([], { hour12: false }), type: evt.type, label: labelForEvent(evt) };
    setLog((prev) => [entry, ...prev].slice(0, MAX_LOG_ENTRIES));
    if (evt.type === "error") {
      setError(evt.message || "System error");
      setOrbState("error");
      clearTimeout(responseTimer.current);
      responseTimer.current = setTimeout(() => {
        setError(null);
        setOrbState("idle");
      }, 30000);
    } else if (evt.type === "capability_started") setOrbDetail(entry.label);
    else if (evt.type === "capability_finished" || evt.type === "agent_thinking") setOrbDetail("");
  }, []);

  useAgentSocket(sessionId, handleEvent);
  const handleFinalTranscript = useCallback((text) => {
    if (!text) return;
    setInputValue(text);
    setVoiceTranscript(text);
    clearTimeout(responseTimer.current);
    responseTimer.current = setTimeout(() => setVoiceTranscript(""), 30000);
  }, []);
  const { isSupported: micSupported, isListening, interimText, error: micError, toggle: toggleMic } = useSpeechToText({
    lang: "en-US",
    onFinalResult: handleFinalTranscript,
  });
  const { isSupported: ttsSupported, speak, stop: stopSpeaking } = useTextToSpeech();

  useEffect(() => {
    const handleOnline = () => {
      setIsOnline(true);
    };

    const handleOffline = () => {
      setIsOnline(false);

      // Stop microphone immediately
      if (isListening) {
        toggleMic();
      }

      // Show connection error
      setVoiceTranscript("Internet disconnected");
      setResponse("");
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [isListening, toggleMic]);

  useEffect(() => {
    let cancelled = false;
    async function refreshTelemetry() {
      const [backendResult, hostResult] = await Promise.allSettled([
        getStatus(),
        fetch("/__jarvis/metrics", { cache: "no-store" }).then((res) => {
          if (!res.ok) throw new Error("Host telemetry unavailable");
          return res.json();
        }),
      ]);
      if (cancelled) return;
      if (backendResult.status === "fulfilled") {
        setStatus(backendResult.value);
        setStatusError(false);
      } else {
        setStatus(null);
        setStatusError(true);
      }
      if (hostResult.status === "fulfilled") {
        // Keep the last good value for an individual metric if Windows briefly
        // returns a null sample while the desktop is switching/focusing.
        setHostMetrics((prev) => ({ ...(prev || {}), ...(hostResult.value || {}) }));
      }
      // Do not erase good readings just because one telemetry poll failed.

    }
    refreshTelemetry();
    const timer = setInterval(refreshTelemetry, 2000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function refreshChats() {
      try {
        const items = await fetchAllChatHistory();
        if (!cancelled) { setChatHistory(items); setHistoryError(null); }
      } catch (err) {
        if (!cancelled) setHistoryError(err.message || "Unable to load chat history.");
      }
    }
    refreshChats();
    const timer = setInterval(refreshChats, 15000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  useEffect(() => {
    const wasSpeaking = previousOrbState.current === "speaking";
    const isNowIdle = orbState === "idle";

    if (wasSpeaking && isNowIdle && response) {
      clearTimeout(responseTimer.current);

      responseTimer.current = setTimeout(() => {
        setResponse(null);
        setVoiceTranscript("");
      }, 30000);
    }

    previousOrbState.current = orbState;
  }, [orbState, response]);

  const metrics = useMemo(() => [
    ["CPU", hostMetrics?.cpu_percent ?? findMetric(status, ["cpu_percent", "cpu_usage", "cpu_usage_percent", "processor_percent", "processor_usage"]), "cyan"],
    ["RAM", hostMetrics?.ram_percent ?? findMetric(status, ["ram_percent", "memory_percent", "memory_usage", "memory_usage_percent", "ram_usage", "ram", "memory"]), "violet"],
    ["GPU", hostMetrics?.gpu_percent ?? findMetric(status, ["gpu_percent", "gpu_usage", "gpu_usage_percent", "gpu_utilization", "gpu"]), "cyan"],
    ["DISK", hostMetrics?.disk_percent ?? findMetric(status, ["disk_percent", "disk_usage", "disk_usage_percent", "disk_used_percent", "disk"]), "pink"],
  ], [status, hostMetrics]);

  const recentChats = useMemo(() => [...chatHistory].sort((a, b) => {
    const ta = typeof a.created_at === "number" ? a.created_at * 1000 : Date.parse(a.created_at || "") || 0;
    const tb = typeof b.created_at === "number" ? b.created_at * 1000 : Date.parse(b.created_at || "") || 0;
    return tb - ta;
  }).slice(0, 5), [chatHistory]);

  const frequentCommands = useMemo(() => {
    const groups = new Map();
    chatHistory.forEach((item) => {
      const raw = commandText(item);
      const key = normalizeCommand(raw);
      if (!key || key === "chat received") return;
      const existing = groups.get(key);
      groups.set(key, { text: existing?.text || raw, count: (existing?.count || 0) + 1 });
    });
    return [...groups.values()].sort((a, b) => b.count - a.count).slice(0, 5);
  }, [chatHistory]);

  function toggleVoiceOutput() {
    setVoiceEnabled((prev) => { const next = !prev; if (!next) stopSpeaking(); return next; });
  }

  useEffect(() => () => { clearTimeout(speakingTimer.current); clearTimeout(responseTimer.current); }, []);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!isOnline) {
      setVoiceTranscript("Internet disconnected");
      setResponse("");
      return;
    }
    const message = inputValue.trim();
    if (!message || orbState === "thinking") return;
    clearTimeout(speakingTimer.current); clearTimeout(responseTimer.current); stopSpeaking(); setError(null); setOrbDetail(""); setOrbState("thinking"); setInputValue(""); setVoiceTranscript(message);
    try {
      const data = await sendChat(message, sessionId);
      setResponse(data);
      clearTimeout(responseTimer.current);
      setOrbState("speaking");
      if (voiceEnabled && ttsSupported) speak(data.response, { onEnd: () => setOrbState("idle") });
      else speakingTimer.current = setTimeout(() => setOrbState("idle"), 1600);
    } catch (err) {
      setError(err.message || "Request failed."); setOrbState("error"); clearTimeout(responseTimer.current); responseTimer.current = setTimeout(() => { setError(null); setVoiceTranscript(""); }, 30000); speakingTimer.current = setTimeout(() => setOrbState("idle"), 1600);
    }
  }

  return (
    <div className="jarvis-dashboard">
      <div className="dashboard-grid">
        <div className="left-stack">
          <Panel title="SYSTEM OVERVIEW" right={status?.database_ok ? "LIVE" : "BACKEND"}>
            <div className="system-gauges">
              {metrics.map(([name, value, tone]) => <Gauge key={name} name={name} value={value} tone={tone} />)}
            </div>
            <div className="system-telemetry">
              <span><i className={`status-mini ${status ? "ok" : "bad"}`} /> Backend {status ? "reachable" : "unavailable"}</span>
              <span>DB {status?.database_ok ? "healthy" : status ? "unhealthy" : "—"}</span>
              <span>Uptime {status?.uptime_seconds != null ? `${Math.floor(status.uptime_seconds / 3600)}h ${Math.floor((status.uptime_seconds % 3600) / 60)}m` : "—"}</span>
              <span>Host telemetry {hostMetrics ? "LIVE" : "WAITING"}</span>
            </div>
          </Panel>

          <Panel title="RECENT TASKS" right={historyError ? "UNAVAILABLE" : "CHAT RECEIVED"}>
            <div className="task-list recent-task-list">
              {recentChats.map((item) => <div key={item.id}><span>◉</span><span className="task-command">{commandText(item)}</span><b>{formatTime(item.created_at)}</b></div>)}
              {recentChats.length === 0 && <div className="empty-inline">{historyError || "No chat requests recorded yet."}</div>}
            </div>
            <button type="button" className="panel-more" onClick={() => onNavigate?.("chat")}>MORE <span>→</span></button>
          </Panel>

          <Panel title="ACTIVE AGENTS" right={`${ACTIVE_AGENTS.filter((agent) => agent.state === "ACTIVE").length} ACTIVE`}>
            <div className="agent-list">
              {ACTIVE_AGENTS.map((agent) => <div className="agent-row" key={agent.name}>
                <span className={`agent-pulse agent-${agent.tone}`} />
                <div><b>{agent.name}</b><small>{agent.task}</small></div>
                <strong>{agent.state}</strong>
              </div>)}
            </div>
          </Panel>
        </div>

        <section className="core-stage">
          {/* <div className="core-header"><span>AI CORE</span><span>LIVE</span></div> */}
          <div className="core-canvas-wrap">
            <VoiceOrb state={(error || micError || historyError || statusError || !isOnline) ? "error" : isListening ? "listening" : orbState} detail={orbDetail} size={700} />
            {(response || error || micError || !isOnline) && (
              <div className={`floating-response ${error || micError || !isOnline ? "is-error" : ""}`} role="status">
                <button type="button" className="response-close" onClick={() => { setResponse(null); setError(null); clearTimeout(responseTimer.current); }} aria-label="Close response">×</button>
                <p>{!isOnline ? "Internet disconnected": error || (micError ? labelForMicError(micError) : response?.response)}</p>
              </div>
            )}
          </div>
          <form className="command-dock" onSubmit={handleSubmit}>
            <button type="button" className={`dock-mic ${isListening ? "active" : ""}`} onClick={toggleMic} disabled={!micSupported || orbState === "thinking" || !isOnline} aria-label="Microphone">🎙️</button>
            <input value={inputValue} onChange={(e) => setInputValue(e.target.value)} autoFocus placeholder= {isOnline? "Speak or type a command for JARVIS..." : "Internet disconnected"} disabled={!isOnline || (orbState === "thinking")} />
            <button type="submit" className="dock-send" disabled={!inputValue.trim() || orbState === "thinking"} aria-label="Send">{orbState === "thinking" ? "···" : "➤"}</button>
            <button type="button" className={`dock-mute ${voiceEnabled ? "unmuted" : "muted"}`} onClick={toggleVoiceOutput} aria-label={voiceEnabled ? "Mute voice output" : "Unmute voice output"}>{voiceEnabled ? "🔊" : "🔇"}</button>
          </form>
        </section>

        <div className="right-stack">
          <Panel title="LIVE INTELLIGENCE FEED" right={<span className="live-text">● LIVE</span>}>
            <div className="feed-list">
              {log.slice(0, 5).map((e) => <div className="feed-row" key={e.id}><span className="feed-icon">◉</span><div><b>{e.label}</b><small>{e.time}</small></div></div>)}
              {log.length === 0 && ["Voice engine ready", "Neural context synchronized", "Awaiting command", "System telemetry online"].map((x, i) => <div className="feed-row" key={x}><span className="feed-icon">{i === 0 ? "◉" : "○"}</span><div><b>{x}</b><small>{i === 0 ? "NOW" : `${i + 1}m`}</small></div></div>)}
            </div>
            <button type="button" className="panel-more" onClick={() => onNavigate?.("history")}>MORE <span>→</span></button>
          </Panel>

          <Panel title="QUICK COMMANDS" right="MOST USED">
            <div className="quick-grid">
              {frequentCommands.map((command) => <button type="button" key={normalizeCommand(command.text)} onClick={() => setInputValue(command.text)}><span className="quick-command-text">{command.text}</span><small>{command.count}×</small><span>›</span></button>)}
              {frequentCommands.length === 0 && <div className="empty-inline">Waiting for chat history...</div>}
            </div>
          </Panel>

          <Panel title="TECH STACK" right="CONFIGURED">
            <div className="tech-grid">
              <div><span>◉</span><b>Groq API</b><small>LLM inference</small></div>
              <div><span>✦</span><b>GPT-OSS 120B</b><small>Primary model</small></div>
              <div><span>◇</span><b>GPT-OSS 20B</b><small>Tool model</small></div>
              <div><span>◌</span><b>Whisper v3 Turbo</b><small>Speech to text</small></div>
              <div><span>◍</span><b>Piper Lessac</b><small>Voice synthesis</small></div>
              <div><span>⌁</span><b>REST + WebSocket</b><small>Assistant transport</small></div>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
