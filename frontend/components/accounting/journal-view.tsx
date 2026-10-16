"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Account,
  Entry,
  EntryList,
  fromPaise,
  callAccounting,
  jsonBody,
  money,
  toPaise,
  todayIso,
} from "./shared";

interface DraftLine {
  key: number;
  account_id: string;
  debit: string;
  credit: string;
  description: string;
}

let nextKey = 1;
function blankLine(): DraftLine {
  nextKey += 1;
  return { key: nextKey, account_id: "", debit: "", credit: "", description: "" };
}

export function JournalListView() {
  const [statusFilter, setStatusFilter] = useState("");
  const [data, setData] = useState<EntryList | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    const query = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
    callAccounting<EntryList>(`/journal-entries${query}`)
      .then((list) => active && setData(list))
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, [statusFilter]);

  return (
    <>
      {message && (
        <p className="form-error" role="alert">
          {message}
        </p>
      )}
      <section className="upload-panel">
        <label className="upload-form">
          Show
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All entries</option>
            <option value="draft">Drafts</option>
            <option value="posted">Posted</option>
          </select>
        </label>
        <p className="small">
          <Link href="/accounting/journal/new">New journal entry →</Link>
        </p>
      </section>
      {data === null && !message && <p className="muted">Loading…</p>}
      {data?.items.length === 0 && <p className="muted">No entries yet.</p>}
      <ul className="document-list">
        {data?.items.map((entry) => (
          <li key={entry.id} className="document-card">
            <div className="document-head">
              <div>
                <Link href={`/accounting/journal/${entry.id}`}>
                  <h3>{entry.entry_number ? `JE-${entry.entry_number}` : "Draft"} · {entry.description}</h3>
                </Link>
                <p className="muted small">
                  Transaction {entry.transaction_date}
                  {entry.posting_date ? ` · Posted ${entry.posting_date}` : ""}
                </p>
              </div>
              <span className={`status-pill ${entry.status === "posted" ? "status-active" : "status-pending"}`}>
                {entry.status === "posted" ? "Posted" : "Draft"}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

export function JournalFormView({ entryId }: { entryId?: string }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [transactionDate, setTransactionDate] = useState(todayIso());
  const [description, setDescription] = useState("");
  const [reference, setReference] = useState("");
  const [lines, setLines] = useState<DraftLine[]>([blankLine(), blankLine()]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(!entryId);

  useEffect(() => {
    let active = true;
    callAccounting<Account[]>("/accounts?include_inactive=false")
      .then((list) => active && setAccounts(list.filter((a) => a.subtype !== "header")))
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!entryId) return;
    let active = true;
    callAccounting<Entry>(`/journal-entries/${encodeURIComponent(entryId)}`)
      .then((entry) => {
        if (!active) return;
        setTransactionDate(entry.transaction_date);
        setDescription(entry.description);
        setReference(entry.reference_id ?? "");
        setLines(
          entry.lines.map((line) => ({
            ...blankLine(),
            account_id: line.account_id,
            debit: line.debit === "0.00" ? "" : line.debit,
            credit: line.credit === "0.00" ? "" : line.credit,
            description: line.description ?? "",
          })),
        );
        setLoaded(true);
      })
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, [entryId]);

  const totals = useMemo(() => {
    let debit = 0;
    let credit = 0;
    let invalid = false;
    for (const line of lines) {
      const d = toPaise(line.debit);
      const c = toPaise(line.credit);
      if (d === null || c === null) invalid = true;
      else {
        debit += d;
        credit += c;
      }
    }
    return { debit, credit, invalid, balanced: !invalid && debit === credit && debit > 0 };
  }, [lines]);

  function update(key: number, patch: Partial<DraftLine>) {
    setLines((current) => current.map((line) => (line.key === key ? { ...line, ...patch } : line)));
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    if (totals.invalid) {
      setMessage("Amounts must be numbers with at most two decimal places.");
      return;
    }
    if (lines.some((line) => !line.account_id && (line.debit.trim() || line.credit.trim()))) {
      setMessage("Choose an account for every line that has an amount.");
      return;
    }
    setBusy(true);
    const body = {
      transaction_date: transactionDate,
      description: description.trim(),
      reference_type: reference ? "manual" : null,
      reference_id: reference.trim() || null,
      lines: lines
        .filter((line) => line.account_id)
        .map((line) => ({
          account_id: line.account_id,
          debit: line.debit.trim() || "0",
          credit: line.credit.trim() || "0",
          description: line.description.trim() || null,
        })),
    };
    try {
      const saved = entryId
        ? await callAccounting<Entry>(`/journal-entries/${encodeURIComponent(entryId)}`, jsonBody("PUT", body))
        : await callAccounting<Entry>("/journal-entries", jsonBody("POST", body));
      window.location.assign(`/accounting/journal/${saved.id}`);
    } catch (error) {
      setMessage((error as Error).message);
      setBusy(false);
    }
  }

  if (!loaded) return <p className="muted">Loading…</p>;

  return (
    <form className="upload-form" onSubmit={save}>
      {message && (
        <p className="form-error" role="alert">
          {message}
        </p>
      )}
      <label>
        Transaction date
        <input type="date" value={transactionDate} onChange={(e) => setTransactionDate(e.target.value)} required />
      </label>
      <label>
        Description
        <input value={description} onChange={(e) => setDescription(e.target.value)} required maxLength={500} />
      </label>
      <label>
        Reference (optional)
        <input value={reference} onChange={(e) => setReference(e.target.value)} maxLength={120} />
      </label>

      <fieldset className="line-editor">
        <legend>Lines</legend>
        {lines.map((line, index) => (
          <div key={line.key} className="line-row">
            <label>
              Account {index + 1}
              <select value={line.account_id} onChange={(e) => update(line.key, { account_id: e.target.value })}>
                <option value="">Choose account</option>
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.code} {account.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Debit
              <input inputMode="decimal" value={line.debit} onChange={(e) => update(line.key, { debit: e.target.value })} placeholder="0.00" />
            </label>
            <label>
              Credit
              <input inputMode="decimal" value={line.credit} onChange={(e) => update(line.key, { credit: e.target.value })} placeholder="0.00" />
            </label>
            <label>
              Note
              <input value={line.description} onChange={(e) => update(line.key, { description: e.target.value })} maxLength={255} />
            </label>
            <button
              className="secondary-button compact"
              type="button"
              onClick={() => setLines((current) => current.filter((item) => item.key !== line.key))}
              disabled={lines.length <= 2}
            >
              Remove line
            </button>
          </div>
        ))}
        <button className="secondary-button" type="button" onClick={() => setLines((current) => [...current, blankLine()])}>
          Add line
        </button>
      </fieldset>

      <p className={totals.balanced ? "hint" : "form-error"} role="status">
        Debit {fromPaise(totals.debit)} · Credit {fromPaise(totals.credit)} —{" "}
        {totals.balanced ? "balanced" : "not balanced yet (posting requires equal totals)"}
      </p>

      <button className="primary-button" disabled={busy}>
        {entryId ? "Save draft" : "Create draft"}
      </button>
    </form>
  );
}

export function JournalDetailView({ entryId }: { entryId: string }) {
  const [entry, setEntry] = useState<Entry | null>(null);
  const [message, setMessage] = useState("");
  const [version, setVersion] = useState(0);
  const [postingDate, setPostingDate] = useState("");
  const [reverseDate, setReverseDate] = useState("");

  useEffect(() => {
    let active = true;
    callAccounting<Entry>(`/journal-entries/${encodeURIComponent(entryId)}`)
      .then((data) => active && setEntry(data))
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, [entryId, version]);

  async function run(action: () => Promise<unknown>, success: string) {
    setMessage("");
    try {
      await action();
      setMessage(success);
      setVersion((v) => v + 1);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  if (!entry) {
    return message ? <p className="form-error" role="alert">{message}</p> : <p className="muted">Loading…</p>;
  }

  const id = encodeURIComponent(entry.id);
  const totalDebit = entry.lines.reduce((sum, line) => sum + (toPaise(line.debit) ?? 0), 0);
  const totalCredit = entry.lines.reduce((sum, line) => sum + (toPaise(line.credit) ?? 0), 0);

  return (
    <>
      {message && (
        <p className="hint" role="status">
          {message}
        </p>
      )}
      <section className="settings-card">
        <h2>{entry.entry_number ? `JE-${entry.entry_number}` : "Draft"}</h2>
        <p>{entry.description}</p>
        <p className="muted small">
          Transaction {entry.transaction_date}
          {entry.posting_date ? ` · Posted ${entry.posting_date}` : ""}
          {entry.reverses_entry_id ? " · Reversal entry" : ""}
        </p>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Account</th>
                <th scope="col" className="num">Debit</th>
                <th scope="col" className="num">Credit</th>
                <th scope="col">Note</th>
              </tr>
            </thead>
            <tbody>
              {entry.lines.map((line) => (
                <tr key={line.id}>
                  <td>{line.account_code}</td>
                  <td className="num">{money(line.debit)}</td>
                  <td className="num">{money(line.credit)}</td>
                  <td>{line.description ?? ""}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">Total</th>
                <td className="num">{fromPaise(totalDebit)}</td>
                <td className="num">{fromPaise(totalCredit)}</td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      {entry.status === "draft" && (
        <section className="settings-card" aria-labelledby="post-title">
          <h2 id="post-title">Draft actions</h2>
          <label className="upload-form">
            Posting date (optional; defaults to the transaction date)
            <input type="date" value={postingDate} onChange={(e) => setPostingDate(e.target.value)} />
          </label>
          <div className="document-actions">
            <Link className="secondary-button compact" href={`/accounting/journal/${id}/edit`}>
              Edit draft
            </Link>
            <button
              className="primary-button compact"
              type="button"
              onClick={() =>
                run(
                  () =>
                    callAccounting(`/journal-entries/${id}/post`, jsonBody("POST", { posting_date: postingDate || null })),
                  "Posted. Posted entries cannot be edited; use a reversal to correct them.",
                )
              }
            >
              Post entry
            </button>
            <button
              className="danger-button compact"
              type="button"
              onClick={() => {
                if (window.confirm("Delete this draft? This cannot be undone.")) {
                  void run(() => callAccounting(`/journal-entries/${id}`, { method: "DELETE" }), "Draft deleted.");
                }
              }}
            >
              Delete draft
            </button>
          </div>
        </section>
      )}

      {entry.status === "posted" && !entry.reverses_entry_id && (
        <section className="settings-card" aria-labelledby="reverse-title">
          <h2 id="reverse-title">Correct with a reversal</h2>
          <p className="small muted">A reversal posts mirrored lines. The original stays unchanged. Choose an OPEN period date.</p>
          <label className="upload-form">
            Reversal posting date (optional)
            <input type="date" value={reverseDate} onChange={(e) => setReverseDate(e.target.value)} />
          </label>
          <button
            className="danger-button compact"
            type="button"
            onClick={() =>
              run(
                () => callAccounting(`/journal-entries/${id}/reverse`, jsonBody("POST", { posting_date: reverseDate || null })),
                "Reversal posted.",
              )
            }
          >
            Post reversal
          </button>
        </section>
      )}
    </>
  );
}
