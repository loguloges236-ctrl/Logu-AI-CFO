"use client";

import { useState, type FormEvent } from "react";
import { callFinance, jsonPost, newId, todayIso, type Account, type Obligation } from "./shared";
import { Notice } from "./views";

// Records one payment against one due period. The reference is generated once per opened form, so a retried
// submit replays the same payment instead of paying twice. The server posts the journal in the same transaction.
export function PaymentForm({ obligation, accounts, duePeriod, onDone }: {
  obligation: Obligation;
  accounts: Account[];
  duePeriod: string;
  onDone: () => void;
}) {
  const [period, setPeriod] = useState(duePeriod);
  const [amount, setAmount] = useState("");
  const [paidOn, setPaidOn] = useState(todayIso());
  const [cashId, setCashId] = useState("");
  const [debitId, setDebitId] = useState("");
  const [reference] = useState(() => newId());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);

  const cashAccounts = accounts.filter((a) => a.is_active && ["cash", "bank", "petty_cash", "other_financial"].includes(a.subtype));
  const debitAccounts = accounts.filter((a) => a.is_active && a.id !== cashId);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await callFinance(`/obligations/${obligation.id}/payments`, jsonPost({
        period_due_date: period, amount, paid_on: paidOn,
        cash_account_id: cashId, debit_account_id: debitId, reference,
      }));
      setMessage({ kind: "ok", text: "Payment recorded and posted to the journal." });
      onDone();
    } catch (failure) {
      setMessage({ kind: "error", text: failure instanceof Error ? failure.message : "Payment could not be recorded." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="fin-form" onSubmit={submit}>
      <label>Due period
        <input type="date" value={period} onChange={(e) => setPeriod(e.target.value)} required />
      </label>
      <label>Amount (₹)
        <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required pattern="^\d{1,16}(\.\d{1,2})?$" />
      </label>
      <label>Paid on
        <input type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} required />
      </label>
      <label>Paid from (cash or bank)
        <select value={cashId} onChange={(e) => setCashId(e.target.value)} required>
          <option value="">Choose account</option>
          {cashAccounts.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}
        </select>
      </label>
      <label>Charge to (expense or liability)
        <select value={debitId} onChange={(e) => setDebitId(e.target.value)} required>
          <option value="">Choose account</option>
          {debitAccounts.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}
        </select>
      </label>
      {message && <Notice kind={message.kind}>{message.text}</Notice>}
      <div className="fin-actions">
        <button className="fin-button primary" type="submit" disabled={busy || !cashId || !debitId}>
          {busy ? "Posting…" : "Record payment"}
        </button>
      </div>
    </form>
  );
}
