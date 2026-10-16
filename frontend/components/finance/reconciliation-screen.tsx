"use client";

import { useState, type FormEvent } from "react";
import { callFinance, jsonPost, todayIso, type BankAccountRow, type Suggestion } from "./shared";
import { Badge, Cards, FinanceFrame, Line, Loading, Notice, Section, useFinance } from "./views";

// Suggestions are never applied automatically. The owner confirms or rejects each one; the decision changes only
// the suggestion's status, and the database blocks any other change to it.
export function ReconciliationScreen() {
  const accounts = useFinance<BankAccountRow[]>("/bank/accounts");
  const suggestions = useFinance<Suggestion[]>("/reconciliation/suggestions?status=suggested");
  const [accountId, setAccountId] = useState("");
  const [since, setSince] = useState(todayIso().slice(0, 8) + "01");
  const [until, setUntil] = useState(todayIso());
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await callFinance("/reconciliation/run", jsonPost({ bank_account_id: accountId, since, until }));
      setMsg({ kind: "ok", text: "Matching finished. New suggestions are listed below." });
      suggestions.reload();
    } catch (failure) {
      setMsg({ kind: "error", text: failure instanceof Error ? failure.message : "Matching could not run." });
    } finally {
      setBusy(false);
    }
  }

  async function decide(id: string, decision: "confirmed" | "rejected") {
    setBusy(true);
    setMsg(null);
    try {
      await callFinance(`/reconciliation/suggestions/${id}/decision`, jsonPost({ decision }));
      suggestions.reload();
    } catch (failure) {
      setMsg({ kind: "error", text: failure instanceof Error ? failure.message : "The decision could not be recorded." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <FinanceFrame title="Reconciliation">
      <Notice kind="warning">Suggestions only. Nothing is matched or changed until you confirm it.</Notice>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <Section title="Run matching">
        <form className="fin-form" onSubmit={run}>
          <label>Bank account
            <select value={accountId} onChange={(e) => setAccountId(e.target.value)} required>
              <option value="">Choose account</option>
              {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.display_name}</option>)}
            </select>
          </label>
          <label>From <input type="date" value={since} onChange={(e) => setSince(e.target.value)} required /></label>
          <label>To <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} required /></label>
          <div className="fin-actions">
            <button className="fin-button primary" type="submit" disabled={busy || !accountId}>Run matching</button>
          </div>
        </form>
      </Section>
      <Loading error={suggestions.error} loading={suggestions.loading} />
      <Section title="Suggestions to review">
        <Cards<Suggestion> rows={suggestions.data ?? []} empty="No suggestions waiting."
          render={(s) => (
            <>
              <Line left={<strong>{s.kind.replace("_", " ")}</strong>} right={<Badge tone="warning">score {s.score}</Badge>} />
              <span className="fin-muted">Bank line {s.bank_transaction_id.slice(0, 8)} · ledger {s.journal_entry_id ? s.journal_entry_id.slice(0, 8) : "none"}</span>
              <div className="fin-actions">
                <button className="fin-button primary" disabled={busy} onClick={() => decide(s.id, "confirmed")}>Confirm</button>
                <button className="fin-button" disabled={busy} onClick={() => decide(s.id, "rejected")}>Reject</button>
              </div>
            </>
          )} />
      </Section>
    </FinanceFrame>
  );
}
