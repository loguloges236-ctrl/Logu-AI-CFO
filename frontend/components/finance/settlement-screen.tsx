"use client";

import { useEffect, useState, type FormEvent } from "react";
import { callAccounting, callFinance, jsonPost, money, newId, todayIso, CASH_SUBTYPES, type Account, type DocumentSummary, type OpenDocument, type Settlement, type SettlementRequest } from "./shared";
import { Badge, Cards, FinanceFrame, Line, Loading, Notice, Section, useFinance } from "./views";

type Msg = { kind: "ok" | "error"; text: string } | null;

function failureText(failure: unknown, fallback: string): string {
  return failure instanceof Error ? failure.message : fallback;
}

export function SettlementScreen() {
  const requests = useFinance<SettlementRequest[]>("/settlement-requests?status=pending");
  const settlements = useFinance<Settlement[]>("/settlements");
  const receivables = useFinance<DocumentSummary>("/receivables");
  const payables = useFinance<DocumentSummary>("/payables");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);

  // New request form
  const [direction, setDirection] = useState<"receivable" | "payable">("receivable");
  const [documentId, setDocumentId] = useState("");
  const [amount, setAmount] = useState("");
  const [settledOn, setSettledOn] = useState(todayIso());
  const [method, setMethod] = useState("bank");
  const [reference, setReference] = useState(() => newId());
  const [cashId, setCashId] = useState("");
  const [controlId, setControlId] = useState("");

  // Reversal form state, one open row at a time
  const [reverseId, setReverseId] = useState<string | null>(null);
  const [reversedOn, setReversedOn] = useState(todayIso());
  const [reason, setReason] = useState("");

  useEffect(() => {
    callAccounting<Account[]>("/accounts").then(setAccounts).catch(() => setAccounts([]));
  }, []);

  const documents: OpenDocument[] = (direction === "receivable" ? receivables.data?.items : payables.data?.items) ?? [];
  const cashAccounts = accounts.filter((a) => a.is_active && CASH_SUBTYPES.includes(a.subtype));
  const activeAccounts = accounts.filter((a) => a.is_active);

  function refreshAll() {
    requests.reload();
    settlements.reload();
    receivables.reload();
    payables.reload();
  }

  async function submitRequest(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await callFinance("/settlement-requests", jsonPost({
        direction, document_id: documentId, amount, settled_on: settledOn, method, reference,
        cash_account_id: cashId, control_account_id: controlId,
      }));
      setMsg({ kind: "ok", text: "Request created. It posts to the journal only after approval." });
      setAmount("");
      setReference(newId());
      refreshAll();
    } catch (failure) {
      setMsg({ kind: "error", text: failureText(failure, "Request could not be created.") });
    } finally {
      setBusy(false);
    }
  }

  async function decide(id: string, action: "approve" | "reject") {
    setBusy(true);
    setMsg(null);
    try {
      await callFinance(`/settlement-requests/${id}/${action}`, jsonPost({}));
      setMsg({ kind: "ok", text: action === "approve" ? "Approved and posted to the journal." : "Request rejected." });
      refreshAll();
    } catch (failure) {
      setMsg({ kind: "error", text: failureText(failure, "The decision could not be recorded.") });
    } finally {
      setBusy(false);
    }
  }

  async function reverse(id: string) {
    setBusy(true);
    setMsg(null);
    try {
      await callFinance(`/settlements/${id}/reverse`, jsonPost({ reversed_on: reversedOn, reason }));
      setMsg({ kind: "ok", text: "Settlement reversed with a mirror journal entry." });
      setReverseId(null);
      setReason("");
      refreshAll();
    } catch (failure) {
      setMsg({ kind: "error", text: failureText(failure, "The settlement could not be reversed.") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <FinanceFrame title="Settlements">
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}

      <Section title="New settlement request">
        <form className="fin-form" onSubmit={submitRequest}>
          <label>Direction
            <select value={direction} onChange={(e) => { setDirection(e.target.value as "receivable" | "payable"); setDocumentId(""); }}>
              <option value="receivable">Customer pays us (receivable)</option>
              <option value="payable">We pay supplier (payable)</option>
            </select>
          </label>
          <label>Document
            <select value={documentId} onChange={(e) => setDocumentId(e.target.value)} required>
              <option value="">Choose posted document</option>
              {documents.map((d) => (
                <option key={d.id} value={d.id}>{d.number} · {d.party} · outstanding {money(d.outstanding)}</option>
              ))}
            </select>
          </label>
          <label>Amount (₹)
            <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required pattern="^\d{1,16}(\.\d{1,2})?$" />
          </label>
          <label>Settled on
            <input type="date" value={settledOn} onChange={(e) => setSettledOn(e.target.value)} required />
          </label>
          <label>Method
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              {["bank", "cash", "upi", "cheque", "card", "other"].map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label>Reference (unique per document)
            <input value={reference} onChange={(e) => setReference(e.target.value)} required maxLength={80} />
          </label>
          <label>Cash or bank account
            <select value={cashId} onChange={(e) => setCashId(e.target.value)} required>
              <option value="">Choose account</option>
              {cashAccounts.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}
            </select>
          </label>
          <label>{direction === "receivable" ? "Receivables control account" : "Payables control account"}
            <select value={controlId} onChange={(e) => setControlId(e.target.value)} required>
              <option value="">Choose account</option>
              {activeAccounts.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}
            </select>
          </label>
          <div className="fin-actions">
            <button className="fin-button primary" type="submit" disabled={busy || !documentId || !cashId || !controlId}>
              Create request
            </button>
          </div>
        </form>
      </Section>

      <Loading error={requests.error} loading={requests.loading} />
      <Section title="Pending requests">
        <Cards<SettlementRequest> rows={requests.data ?? []} empty="No pending requests."
          render={(r) => (
            <>
              <Line left={<strong>{r.direction === "receivable" ? "Receivable" : "Payable"} · {r.reference}</strong>} right={money(r.amount)} />
              <span className="fin-muted">{r.method} · {r.settled_on}</span>
              <div className="fin-actions">
                <button className="fin-button primary" disabled={busy} onClick={() => decide(r.id, "approve")}>Approve</button>
                <button className="fin-button" disabled={busy} onClick={() => decide(r.id, "reject")}>Reject</button>
              </div>
            </>
          )} />
      </Section>

      <Loading error={settlements.error} loading={settlements.loading} />
      <Section title="Recorded settlements">
        <Cards<Settlement> rows={settlements.data ?? []} empty="No settlements yet."
          render={(s) => (
            <>
              <Line left={<strong>{s.reference}</strong>} right={money(s.amount)} />
              <Line left={<span className="fin-muted">{s.direction} · {s.settled_on} · {s.method}</span>}
                right={<Badge tone={s.state === "completed" ? "ok" : "warning"}>{s.state}</Badge>} />
              {s.state === "completed" && (
                reverseId === s.id ? (
                  <div className="fin-form">
                    <label>Reversal date
                      <input type="date" value={reversedOn} onChange={(e) => setReversedOn(e.target.value)} />
                    </label>
                    <label>Reason
                      <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200} />
                    </label>
                    <div className="fin-actions">
                      <button className="fin-button primary" disabled={busy || !reason} onClick={() => reverse(s.id)}>Confirm reversal</button>
                      <button className="fin-button" onClick={() => setReverseId(null)}>Cancel</button>
                    </div>
                  </div>
                ) : (
                  <div className="fin-actions">
                    <button className="fin-button" onClick={() => setReverseId(s.id)}>Reverse</button>
                  </div>
                )
              )}
            </>
          )} />
      </Section>
    </FinanceFrame>
  );
}
