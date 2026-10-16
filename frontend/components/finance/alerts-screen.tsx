"use client";

import { useState } from "react";
import { callFinance, jsonPost, dateText, type AlertNotice } from "./shared";
import { Badge, Cards, FinanceFrame, Line, Loading, Notice, Section, useFinance } from "./views";

export function AlertsScreen() {
  const { data, error, loading, reload } = useFinance<AlertNotice[]>("/notifications");
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function runReminders() {
    setBusy(true);
    setMessage(null);
    try {
      const result = await callFinance<{ created?: number }>("/reminders/run", jsonPost({}));
      setMessage({ kind: "ok", text: `Reminder check finished${typeof result?.created === "number" ? `: ${result.created} new` : ""}.` });
      reload();
    } catch (failure) {
      setMessage({ kind: "error", text: failure instanceof Error ? failure.message : "Reminder check failed." });
    } finally {
      setBusy(false);
    }
  }

  async function markRead(id: string) {
    try {
      await callFinance(`/notifications/${id}/read`, jsonPost({}));
      reload();
    } catch (failure) {
      setMessage({ kind: "error", text: failure instanceof Error ? failure.message : "Could not mark as read." });
    }
  }

  return (
    <FinanceFrame title="Alerts and reminders">
      <div className="fin-actions">
        <button className="fin-button primary" onClick={runReminders} disabled={busy}>
          {busy ? "Checking…" : "Run reminder check"}
        </button>
      </div>
      {message && <Notice kind={message.kind}>{message.text}</Notice>}
      <Notice kind="warning">Alerts are shown in the app only. No email, SMS or push is sent: no delivery provider is connected.</Notice>
      <Loading error={error} loading={loading} />
      <Section title="Notifications">
        <Cards<AlertNotice> rows={data ?? []} empty="No alerts."
          render={(n) => (
            <>
              <Line left={<strong>{n.title}</strong>} right={n.read ? <Badge tone="ok">Read</Badge> : <Badge tone="warning">New</Badge>} />
              <span className="fin-muted">{n.body}</span>
              <Line left={<span className="fin-muted">due {dateText(n.due_date)}</span>}
                right={n.read ? null : <button className="fin-button" onClick={() => markRead(n.id)}>Mark read</button>} />
            </>
          )} />
      </Section>
    </FinanceFrame>
  );
}
