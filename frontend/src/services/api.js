const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json();
}

export function getHealth() {
  return request("/health");
}

export function getActions(limit = 10) {
  return request(`/actions?limit=${limit}`);
}

export { API_BASE_URL };

export function sendChat(message, sessionId = "default") {
  return request("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
}

export function getHistory({ sessionId, type, limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams();
  if (sessionId) params.set("session_id", sessionId);
  if (type) params.set("type", type);
  params.set("limit", limit);
  params.set("offset", offset);
  return request(`/history?${params.toString()}`);
}

export function getConversations({ limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit, offset });
  return request(`/conversations?${params.toString()}`);
}

export function getConversation(sessionId) {
  return request(`/conversations/${sessionId}`);
}

export function getActionsSummary() {
  // We only need the `total` field — limit=1 keeps the payload tiny.
  return request(`/history?limit=1&offset=0`);
}

export function getStatus() {
  return request(`/status`);
}
