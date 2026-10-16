"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import {
  Account,
  CATEGORY_LABELS,
  Category,
  Ledger,
  SUBTYPES,
  callAccounting,
  jsonBody,
  money,
} from "./shared";

const CATEGORIES = Object.keys(CATEGORY_LABELS) as Category[];

export function AccountsView() {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [version, setVersion] = useState(0);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState<Category>("asset");
  const [subtype, setSubtype] = useState("general");
  const [parentId, setParentId] = useState("");

  useEffect(() => {
    let active = true;
    callAccounting<Account[]>("/accounts")
      .then((list) => {
        if (active) setAccounts(list);
      })
      .catch((error: Error) => {
        if (active) setMessage(error.message);
      });
    return () => {
      active = false;
    };
  }, [version]);

  async function setup() {
    setBusy(true);
    setMessage("");
    try {
      await callAccounting<{ created: number }>("/setup", { method: "POST" });
      setMessage("Default chart of accounts created. Adjust or add accounts as needed.");
      setVersion((v) => v + 1);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      await callAccounting<Account>(
        "/accounts",
        jsonBody("POST", {
          code: code.trim(),
          name: name.trim(),
          category,
          subtype,
          parent_id: parentId || null,
        }),
      );
      setCode("");
      setName("");
      setParentId("");
      setMessage("Account created.");
      setVersion((v) => v + 1);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const headers = (accounts ?? []).filter((a) => a.subtype === "header" && a.category === category);

  return (
    <>
      {message && (
        <p className="hint" role="status">
          {message}
        </p>
      )}

      {accounts !== null && accounts.length === 0 && (
        <section className="settings-card">
          <h2>Start with a default chart</h2>
          <p>A generic template with assets, liabilities, equity, income and expense groups. It is not your business&apos;s chart; edit or add accounts afterwards.</p>
          <button className="primary-button" type="button" onClick={setup} disabled={busy}>
            Create default chart
          </button>
        </section>
      )}

      <section className="document-list-section" aria-labelledby="accounts-title">
        <h2 id="accounts-title">Chart of accounts</h2>
        {accounts === null && <p className="muted">Loading…</p>}
        <ul className="plain-list">
          {accounts?.map((account) => (
            <li key={account.id} className="row-item">
              <div>
                <Link href={`/accounting/accounts/${account.id}`}>
                  <strong>
                    {account.code} {account.name}
                  </strong>
                </Link>
                <p className="muted small">
                  {CATEGORY_LABELS[account.category]} · {account.subtype.replace(/_/g, " ")}
                  {account.is_active ? "" : " · inactive"}
                  {account.is_system ? " · default" : ""}
                </p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="upload-panel" aria-labelledby="new-account-title">
        <h2 id="new-account-title">Add account</h2>
        <form className="upload-form" onSubmit={create}>
          <label>
            Code (4 to 10 digits)
            <input value={code} onChange={(e) => setCode(e.target.value)} required pattern="[0-9]{4,10}" inputMode="numeric" />
          </label>
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
          </label>
          <label>
            Category
            <select value={category} onChange={(e) => setCategory(e.target.value as Category)}>
              {CATEGORIES.map((value) => (
                <option key={value} value={value}>
                  {CATEGORY_LABELS[value]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Type
            <select value={subtype} onChange={(e) => setSubtype(e.target.value)}>
              {SUBTYPES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Group under (optional)
            <select value={parentId} onChange={(e) => setParentId(e.target.value)}>
              <option value="">None</option>
              {headers.map((header) => (
                <option key={header.id} value={header.id}>
                  {header.code} {header.name}
                </option>
              ))}
            </select>
          </label>
          <button className="primary-button" disabled={busy}>
            Create account
          </button>
        </form>
      </section>
    </>
  );
}

export function AccountDetailView({ accountId }: { accountId: string }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [version, setVersion] = useState(0);
  const [message, setMessage] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [subtype, setSubtype] = useState("general");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");

  useEffect(() => {
    let active = true;
    callAccounting<Account>(`/accounts/${encodeURIComponent(accountId)}`)
      .then((data) => {
        if (!active) return;
        setAccount(data);
        setName(data.name);
        setDescription(data.description ?? "");
        setSubtype(data.subtype);
      })
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, [accountId, version]);

  useEffect(() => {
    let active = true;
    const query = new URLSearchParams();
    if (start) query.set("start", start);
    if (end) query.set("end", end);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    callAccounting<Ledger>(`/accounts/${encodeURIComponent(accountId)}/ledger${suffix}`)
      .then((data) => active && setLedger(data))
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, [accountId, start, end, version]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await callAccounting<Account>(
        `/accounts/${encodeURIComponent(accountId)}`,
        jsonBody("PATCH", { name: name.trim(), description, subtype }),
      );
      setMessage("Saved.");
      setVersion((v) => v + 1);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function toggleActive() {
    if (!account) return;
    try {
      await callAccounting<Account>(
        `/accounts/${encodeURIComponent(accountId)}`,
        jsonBody("PATCH", { is_active: !account.is_active }),
      );
      setMessage(account.is_active ? "Account deactivated. Its history is kept." : "Account activated.");
      setVersion((v) => v + 1);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  if (!account) {
    return message ? <p className="form-error" role="alert">{message}</p> : <p className="muted">Loading…</p>;
  }

  return (
    <>
      {message && (
        <p className="hint" role="status">
          {message}
        </p>
      )}
      <section className="settings-card">
        <h2>
          {account.code} {account.name}
        </h2>
        <p className="muted">
          {CATEGORY_LABELS[account.category]} · {account.subtype.replace(/_/g, " ")} · {account.is_active ? "Active" : "Inactive"}
        </p>
        <p className="small muted">The code and category are fixed. They affect reported history.</p>
        <button className="secondary-button" type="button" onClick={toggleActive}>
          {account.is_active ? "Deactivate" : "Activate"}
        </button>
      </section>

      <section className="upload-panel" aria-labelledby="edit-account-title">
        <h2 id="edit-account-title">Edit</h2>
        <form className="upload-form" onSubmit={save}>
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
          </label>
          <label>
            Description
            <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} />
          </label>
          <label>
            Type
            <select value={subtype} onChange={(e) => setSubtype(e.target.value)}>
              {SUBTYPES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <button className="primary-button">Save</button>
        </form>
      </section>

      <LedgerSection
        ledger={ledger}
        start={start}
        end={end}
        onStart={setStart}
        onEnd={setEnd}
      />
    </>
  );
}

interface LedgerSectionProps {
  ledger: Ledger | null;
  start: string;
  end: string;
  onStart: (value: string) => void;
  onEnd: (value: string) => void;
}

function LedgerSection({ ledger, start, end, onStart, onEnd }: LedgerSectionProps) {
  return (
    <section className="document-list-section" aria-labelledby="ledger-title">
      <h2 id="ledger-title">Account history</h2>
      <div className="upload-form">
        <label>
          From
          <input type="date" value={start} onChange={(e) => onStart(e.target.value)} />
        </label>
        <label>
          To
          <input type="date" value={end} onChange={(e) => onEnd(e.target.value)} />
        </label>
      </div>
      {ledger === null ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <p className="small muted">
            Opening {money(ledger.opening_balance)} · Debits {money(ledger.debit_total)} · Credits {money(ledger.credit_total)} · Closing {money(ledger.closing_balance)}
          </p>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Entry</th>
                  <th scope="col">Description</th>
                  <th scope="col" className="num">Debit</th>
                  <th scope="col" className="num">Credit</th>
                  <th scope="col" className="num">Balance</th>
                </tr>
              </thead>
              <tbody>
                {ledger.rows.map((row, index) => (
                  <tr key={index}>
                    <td>{row.posting_date}</td>
                    <td>#{row.entry_number}</td>
                    <td>{row.description}</td>
                    <td className="num">{money(row.debit)}</td>
                    <td className="num">{money(row.credit)}</td>
                    <td className="num">{money(row.balance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {ledger.rows.length === 0 && <p className="muted">No posted movements in this range.</p>}
          </div>
        </>
      )}
    </section>
  );
}

export function LedgerPageView({
  accountId,
  start,
  end,
}: {
  accountId: string;
  start: string;
  end: string;
}) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [selected, setSelected] = useState(accountId);
  const [from, setFrom] = useState(start);
  const [to, setTo] = useState(end);
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    callAccounting<Account[]>("/accounts")
      .then((list) => {
        if (!active) return;
        setAccounts(list);
        setSelected((current) => current || list[0]?.id || "");
      })
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    setLedger(null);
    const query = new URLSearchParams();
    if (from) query.set("start", from);
    if (to) query.set("end", to);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    callAccounting<Ledger>(`/accounts/${encodeURIComponent(selected)}/ledger${suffix}`)
      .then((data) => active && setLedger(data))
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, [selected, from, to]);

  return (
    <>
      {message && (
        <p className="form-error" role="alert">
          {message}
        </p>
      )}
      <section className="upload-panel">
        <div className="upload-form">
          <label>
            Account
            <select value={selected} onChange={(e) => setSelected(e.target.value)}>
              {(accounts ?? []).map((account) => (
                <option key={account.id} value={account.id}>
                  {account.code} {account.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            From
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label>
            To
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
        </div>
      </section>
      <LedgerSection ledger={ledger} start={from} end={to} onStart={setFrom} onEnd={setTo} />
    </>
  );
}
