import { useEffect, useState } from "react";
import { getConversations, getConversation } from "../services/api";

const PAGE_SIZE = 50;

function formatTime(unixSeconds) {
  return new Date(unixSeconds * 1000).toLocaleString([], { hour12: false });
}

export default function ConversationsPage({ onResumeSession }) {
  const [list, setList] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState(null);

  const [selected, setSelected] = useState(null); // session_id
  const [messages, setMessages] = useState([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(null);

  async function loadList(nextOffset = 0) {
    setListLoading(true);
    setListError(null);
    try {
      const data = await getConversations({ limit: PAGE_SIZE, offset: nextOffset });
      setList(nextOffset === 0 ? data.items : (prev) => [...prev, ...data.items]);
      setTotal(data.total);
      setOffset(nextOffset);
    } catch (err) {
      setListError(err.message || "Failed to load conversations.");
    } finally {
      setListLoading(false);
    }
  }

  async function openConversation(sessionId) {
    setSelected(sessionId);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const data = await getConversation(sessionId);
      setMessages(data.messages);
    } catch (err) {
      setDetailError(err.message || "Failed to load transcript.");
      setMessages([]);
    } finally {
      setDetailLoading(false);
    }
  }

  useEffect(() => {
    loadList(0);
  }, []);

  const hasMore = list.length < total;

  return (
    <div className="conversations-page">
      <div className="conversations-list-pane">
        <h2>Conversations</h2>

        {listError && <div className="conversations-error">⚠ {listError}</div>}
        {!listError && list.length === 0 && !listLoading && (
          <div className="conversations-empty">No conversations yet.</div>
        )}

        <ul className="conversations-list">
          {list.map((c) => (
            <li
              key={c.session_id}
              className={`conversation-row ${selected === c.session_id ? "active" : ""}`}
              onClick={() => openConversation(c.session_id)}
            >
              <div className="conversation-preview">{c.preview || "(empty)"}</div>
              <div className="conversation-meta">
                <span>{c.message_count} msgs</span>
                <span>{formatTime(c.last_at)}</span>
              </div>
            </li>
          ))}
        </ul>

        {hasMore && (
          <button
            className="conversations-load-more"
            onClick={() => loadList(offset + PAGE_SIZE)}
            disabled={listLoading}
          >
            {listLoading ? "Loading..." : "Load more"}
          </button>
        )}
      </div>

      <div className="conversations-detail-pane">
        {!selected && <div className="conversations-empty">Select a conversation to view it.</div>}

        {selected && (
          <>
            <div className="conversations-detail-header">
              <span className="conversations-detail-id">{selected.slice(0, 12)}...</span>
              {onResumeSession && (
                <button
                  className="conversations-resume-btn"
                  onClick={() => onResumeSession(selected)}
                >
                  Resume this conversation
                </button>
              )}
            </div>

            {detailError && <div className="conversations-error">⚠ {detailError}</div>}
            {detailLoading && <div className="conversations-empty">Loading transcript...</div>}

            <div className="conversations-transcript">
              {messages.map((m) => (
                <div key={m.id} className={`transcript-bubble transcript-${m.role}`}>
                  <div className="transcript-role">{m.role}</div>
                  <div className="transcript-text">{m.text}</div>
                  <div className="transcript-time">{formatTime(m.created_at)}</div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
