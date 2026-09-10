import { useEffect, useState } from "react";
import { getConversations, getActionsSummary } from "../services/api";

const POLL_INTERVAL_MS = 15000;

/** Polls global conversation/action totals for the sidebar badges.
 * Failures are swallowed — badges just keep their last known value
 * rather than breaking the UI over a non-critical count. */
export function useBadgeCounts() {
  const [conversationCount, setConversationCount] = useState(0);
  const [actionCount, setActionCount] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const [conversations, actions] = await Promise.all([
          getConversations({ limit: 1, offset: 0 }),
          getActionsSummary(),
        ]);
        if (cancelled) return;
        setConversationCount(conversations.total ?? 0);
        setActionCount(actions.total ?? 0);
      } catch {
        // silent — non-critical
      }
    }

    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return { conversationCount, actionCount };
}
