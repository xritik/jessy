import { useEffect, useState } from "react";
import { getHistory } from "../services/api";

const PAGE_SIZE = 50;

const TYPE_OPTIONS = [
  { value: "", label: "All types" },
  { value: "chat_received", label: "Chat" },
  { value: "agent_thinking", label: "Reasoning" },
  { value: "capability_started", label: "Capability started" },
  { value: "capability_finished", label: "Capability finished" },
  { value: "error", label: "Errors" },
];

function formatTime(unixSeconds) {
  return new Date(unixSeconds * 1000).toLocaleString([], {
    hour12: false,
  });
}

export default function ActionHistoryPage({ sessionId }) {
  const [scope, setScope] = useState("session"); // "session" | "all"
  const [typeFilter, setTypeFilter] = useState("");
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function load(nextOffset = 0) {
    setLoading(true);
    setError(null);
    try {
      const data = await getHistory({
        sessionId: scope === "session" ? sessionId : undefined,
        type: typeFilter || undefined,
        limit: PAGE_SIZE,
        offset: nextOffset,
      });
      if (nextOffset === 0) setItems(data.items);
      else setItems((prev) => [...prev, ...data.items]);
      setTotal(data.total);
      setOffset(nextOffset);
    } catch (err) {
      setError(err.message || "Failed to load history.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, typeFilter, sessionId]);

  const hasMore = items.length < total;

  return (
    <div className="history-page">
      <div className="history-header">
        <h2>Action History</h2>
        <div className="history-controls">
          <div className="history-toggle">
            <button
              className={scope === "session" ? "active" : ""}
              onClick={() => setScope("session")}
            >
              This session
            </button>
            <button className={scope === "all" ? "active" : ""} onClick={() => setScope("all")}>
              All sessions
            </button>
          </div>
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            {TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <button className="history-refresh" onClick={() => load(0)} disabled={loading}>
            {loading ? "···" : "Refresh"}
          </button>
        </div>
      </div>

      {error && <div className="history-error">⚠ {error}</div>}

      {!error && items.length === 0 && !loading && (
        <div className="history-empty">No actions recorded yet.</div>
      )}

      <ul className="history-list">
        {items.map((item) => (
          <li key={item.id} className={`history-row history-type-${item.type}`}>
            <span className="history-time">{formatTime(item.created_at)}</span>
            <span className="history-type-badge">{item.type.replace(/_/g, " ")}</span>
            <span className="history-detail">
              {item.capability && <strong>{item.capability}</strong>}
              {item.detail && ` — ${item.detail}`}
            </span>
            {scope === "all" && (
              <span className="history-session">{item.session_id.slice(0, 8)}</span>
            )}
          </li>
        ))}
      </ul>

      {hasMore && (
        <button className="history-load-more" onClick={() => load(offset + PAGE_SIZE)} disabled={loading}>
          {loading ? "Loading..." : `Load more (${items.length}/${total})`}
        </button>
      )}
    </div>
  );
}
