import { useEffect, useState } from "react";

const BACKEND_URL = "http://localhost:8000";

/**
 * Step 1 App Shell.
 *
 * This is NOT the final Solar System Command Center GUI — that is
 * built in Phase 11. This shell exists only to prove that the
 * frontend can reach the real backend, using the brand colors
 * and identity that the final GUI will be built on top of.
 */
function App() {
  const [status, setStatus] = useState("CONNECTING");
  const [timestamp, setTimestamp] = useState(null);

  useEffect(() => {
    const checkHealth = async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/health`);
        if (!res.ok) throw new Error("Bad response");
        const data = await res.json();
        setStatus(data.status === "online" ? "OPTIMAL" : "DEGRADED");
        setTimestamp(data.timestamp);
      } catch (err) {
        setStatus("DEGRADED");
      }
    };

    checkHealth();
    const interval = setInterval(checkHealth, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="jessy-shell">
      <h1 className="jessy-title">JESSY</h1>
      <p className="jessy-subtitle">AI CORE · v0.1 (Foundation)</p>

      <div className={`status-pill status-${status.toLowerCase()}`}>
        <span className="status-dot" />
        {status}
      </div>

      {timestamp && (
        <p className="jessy-timestamp">Backend last responded: {timestamp}</p>
      )}
    </div>
  );
}

export default App;
