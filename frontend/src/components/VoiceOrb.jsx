/**
 * Central animated orb — the visual heartbeat of the app.
 *
 * state: "idle" | "thinking" | "speaking" | "listening" | "error"
 * detail: optional live sub-label (e.g. "web_search running"),
 *         fed by real WebSocket progress events from Step 11.3.
 */
export default function VoiceOrb({ state = "idle", detail = "", size = 220 }) {
  return (
    <div
      className={`voice-orb voice-orb-${state}`}
      style={{ width: size, height: size }}
    >
      <div className="orb-ring orb-ring-outer" />
      <div className="orb-ring orb-ring-mid" />
      <div className="orb-core">
        <span className="orb-core-label">JESSY</span>
        <span className="orb-core-state">{state.toUpperCase()}</span>
        {detail && <span className="orb-core-detail">{detail}</span>}
      </div>
    </div>
  );
}
