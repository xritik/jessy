import { useEffect, useState } from "react";
import { getStatus } from "../services/api";
import { useAgentSocket } from "../hooks/useAgentSocket";

const POLL_INTERVAL_MS = 10000;

function formatUptime(seconds) {
  const s = Math.floor(seconds % 60);
  const m = Math.floor((seconds / 60) % 60);
  const h = Math.floor(seconds / 3600);
  return `${h}h ${m}m ${s}s`;
}

export default function StatusPage({ sessionId }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const { connected } = useAgentSocket(sessionId, () => {});

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const data = await getStatus();
        if (!cancelled) {
          setStatus(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err.message || "Backend unreachable.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const rows = [
    { label: "Backend API", ok: !error, value: error ? "unreachable" : "reachable" },
    {
      label: "Database",
      ok: status?.database_ok ?? false,
      value: status?.database_ok ? "healthy" : "unhealthy",
    },
    { label: "WebSocket (this tab)", ok: connected, value: connected ? "live" : "offline" },
    {
      label: "Server uptime",
      ok: true,
      value: status ? formatUptime(status.uptime_seconds) : "—",
    },
  ];

  return (
    <div className="status-page">
      <h2>System Status</h2>

      {loading && <div className="status-empty">Checking systems...</div>}

      {!loading && (
        <ul className="status-list">
          {rows.map((row) => (
            <li key={row.label} className="status-row">
              <span className={`status-dot ${row.ok ? "status-ok" : "status-bad"}`} />
              <span className="status-label">{row.label}</span>
              <span className="status-value">{row.value}</span>
            </li>
          ))}
        </ul>
      )}

      {error && <div className="status-error">⚠ {error}</div>}
    </div>
  );
}
