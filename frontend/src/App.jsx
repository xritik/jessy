import { useState } from "react";
import StarField from "./components/StarField";
import Sidebar from "./components/Sidebar";
import Topbar from "./components/Topbar";
import Dashboard from "./components/Dashboard";
import ActionHistoryPage from "./pages/ActionHistoryPage";
import ConversationsPage from "./pages/ConversationsPage";
import StatusPage from "./pages/statusPage";
import { useBadgeCounts } from "./hooks/useBadgeCounts";
import "./styles/variables.css";
import "./styles/global.css";
import "./styles/components.css";
import "./styles/dashboard.css";
import "./styles/history.css";
import "./styles/conversations.css";
import "./styles/status.css";

const VIEW_LABELS = {
  chat: "CONVERSATIONS",
  history: "ACTION HISTORY",
};

function makeSessionId() {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `session-${Date.now()}`;
}

export default function App() {
  const [activeView, setActiveView] = useState("dashboard");
  const [searchTerm, setSearchTerm] = useState("");
  const [sessionId, setSessionId] = useState(makeSessionId);
  const { conversationCount, actionCount } = useBadgeCounts();

  return (
    <div className="app-shell">
      <StarField />
      <div className="grid-overlay" aria-hidden="true" />

      <Sidebar
        activeView={activeView}
        onNavigate={setActiveView}
        conversationCount={conversationCount}
        actionCount={actionCount}
      />

      <div className="app-main">
        <Topbar onSearch={setSearchTerm} />

        <main className="app-content">
          {activeView === "dashboard" ? (
            <Dashboard sessionId={sessionId} />
          ) : activeView === "chat" ? (
            <ConversationsPage
              onResumeSession={(id) => {
                setSessionId(id);
                setActiveView("dashboard");
              }}
            />
          ) : activeView === "history" ? (
            <ActionHistoryPage sessionId={sessionId} />
          ) : activeView === "status" ? (
            <StatusPage sessionId={sessionId} />
          ) : (
            <div className="view-placeholder">
              <span>[ {VIEW_LABELS[activeView] ?? activeView.toUpperCase()} VIEW — under construction ]</span>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
