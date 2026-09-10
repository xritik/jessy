import { useEffect, useState } from "react";
import { getHealth } from "../services/api";

const STATUS_LABEL = {
  OPTIMAL: "OPTIMAL",
  DEGRADED: "DEGRADED",
  CONNECTING: "CONNECTING",
};

function formatClock(date) {
  return date.toLocaleTimeString("en-GB", { hour12: false });
}

function formatDate(date) {
  return date.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

/**
 * Polls the real /health endpoint every 5s. Status is never faked:
 * CONNECTING until the first check resolves, OPTIMAL if the backend
 * responds, DEGRADED if it doesn't.
 */
export default function Topbar({ onSearch }) {
  const [now, setNow] = useState(new Date());
  const [status, setStatus] = useState("CONNECTING");

  useEffect(() => {
    const clockTimer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(clockTimer);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        await getHealth();
        if (!cancelled) setStatus("OPTIMAL");
      } catch {
        if (!cancelled) setStatus("DEGRADED");
      }
    }

    poll();
    const healthTimer = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(healthTimer);
    };
  }, []);

  return (
    <header className="topbar">
      <div className="topbar-left">
        <span className={`status-pill status-${status.toLowerCase()}`}>
          <span className="status-dot" />
          {STATUS_LABEL[status]}
        </span>
      </div>

      <div className="topbar-center">
        <span className="topbar-date">{formatDate(now)}</span>
        <span className="topbar-clock">{formatClock(now)}</span>
      </div>

      <div className="topbar-right">
        <input
          type="text"
          className="topbar-search"
          placeholder="Search..."
          onChange={(e) => onSearch?.(e.target.value)}
        />
      </div>
    </header>
  );
}
