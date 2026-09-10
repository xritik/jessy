const NAV_ITEMS = [
  { id: "dashboard", label: "Command Center", icon: "◆" },
  { id: "chat", label: "Conversations", icon: "💬", badgeKey: "conversations" },
  { id: "status", label: "System Status", icon: "◎" },
  { id: "history", label: "Action History", icon: "≡", badgeKey: "actions" },
  { id: "contacts", label: "Contacts", icon: "◈", disabled: true, note: "Soon" },
  { id: "integrations", label: "Integrations", icon: "⚙", disabled: true, note: "Soon" },
];

export default function Sidebar({
  activeView,
  onNavigate,
  conversationCount = 0,
  actionCount = 0,
}) {
  const badgeValues = {
    conversations: conversationCount,
    actions: actionCount,
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="brand-title">JESSY</span>
        <span className="brand-sub">COMMAND CENTER</span>
      </div>

      <nav className="sidebar-nav">
        {NAV_ITEMS.map((item) => {
          const isActive = activeView === item.id;
          const badge = item.badgeKey ? badgeValues[item.badgeKey] : null;

          return (
            <button
              key={item.id}
              type="button"
              className={[
                "nav-item",
                isActive ? "nav-item-active" : "",
                item.disabled ? "nav-item-disabled" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              disabled={item.disabled}
              onClick={() => !item.disabled && onNavigate(item.id)}
            >
              <span className="nav-icon">{item.icon}</span>
              <span className="nav-label">{item.label}</span>
              {badge !== null && badge > 0 && <span className="nav-badge">{badge}</span>}
              {item.note && <span className="nav-note">{item.note}</span>}
            </button>
          );
        })}
      </nav>

      <div className="sidebar-bottom" />
    </aside>
  );
}
