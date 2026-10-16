"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import {
  BalanceSheet,
  Dashboard,
  Period,
  ProfitAndLoss,
  TrialBalance,
  callAccounting,
  jsonBody,
  money,
  todayIso,
} from "./shared";

export function DashboardView() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    callAccounting<Dashboard>("/dashboard")
      .then((value) => active && setData(value))
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, []);

  if (!data) {
    return message ? <p className="form-error" role="alert">{message}</p> : <p className="muted">Loading…</p>;
  }

  return (
    <>
      <section className="card-grid" aria-label="Accounting summary">
        <article className="module-card">
          <span className="card-state">Cash and bank</span>
          <h2>{money(data.cash_total)}</h2>
          <p>Across {data.cash_accounts} cash or bank account(s). Ledger figures only; no bank connection.</p>
          <Link href="/accounting/ledger">View ledger →</Link>
        </article>
        <article className="module-card">
          <span className="card-state">Journal</span>
          <h2>{data.draft_entries} draft · {data.posted_entries} posted</h2>
          <p>Drafts can be edited. Posted entries change only through reversal.</p>
          <Link href="/accounting/journal">Open journal →</Link>
        </article>
        <article className="module-card">
          <span className="card-state">Open period</span>
          <h2>{data.open_period ?? "None for today"}</h2>
          <p>Posting needs an OPEN period that covers the posting date.</p>
          <Link href="/accounting/periods">Manage periods →</Link>
        </article>
        <article className="module-card">
          <span className="card-state">Integrity</span>
          <h2>{data.trial_balance_balanced && data.balance_sheet_balanced ? "Balanced" : "Check needed"}</h2>
          <p>Trial balance and balance sheet equation, as of today.</p>
          <Link href="/accounting/trial-balance">Trial balance →</Link>
        </article>
      </section>
      <section className="settings-card">
        <h2>Reports</h2>
        <p>
          <Link href="/accounting/profit-and-loss">Profit and loss →</Link>
        </p>
        <p>
          <Link href="/accounting/balance-sheet">Balance sheet →</Link>
        </p>
        <p>
          <Link href="/accounting/accounts">Chart of accounts →</Link>
        </p>
      </section>
      {message && <p className="form-error" role="alert">{message}</p>}
    </>
  );
}

export function PeriodsView() {
  const [periods, setPeriods] = useState<Period[] | null>(null);
  const [version, setVersion] = useState(0);
  const [message, setMessage] = useState("");
  const [name, setName] = useState("");
  const [start, setStart] = useState(todayIso().slice(0, 8) + "01");
  const [end, setEnd] = useState("");

  useEffect(() => {
    let active = true;
    callAccounting<Period[]>("/periods")
      .then((list) => active && setPeriods(list))
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, [version]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    try {
      await callAccounting<Period>("/periods", jsonBody("POST", { name: name.trim(), start_date: start, end_date: end }));
      setName("");
      setMessage("Period created.");
      setVersion((v) => v + 1);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function close(id: string, periodName: string) {
    if (!window.confirm(`Close ${periodName}? Closed periods cannot be reopened or posted into. Corrections need a reversal in an open period.`)) {
      return;
    }
    setMessage("");
    try {
      await callAccounting<Period>(`/periods/${encodeURIComponent(id)}/close`, { method: "POST" });
      setMessage(`${periodName} closed.`);
      setVersion((v) => v + 1);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <>
      {message && (
        <p className="hint" role="status">
          {message}
        </p>
      )}
      <section className="document-list-section">
        <h2>Periods</h2>
        {periods === null && <p className="muted">Loading…</p>}
        {periods?.length === 0 && <p className="muted">No periods yet. Create one below.</p>}
        <ul className="plain-list">
          {periods?.map((period) => (
            <li key={period.id} className="row-item">
              <div>
                <strong>{period.name}</strong>
                <p className="muted small">
                  {period.start_date} to {period.end_date} · {period.status === "open" ? "Open" : "Closed"}
                </p>
              </div>
              {period.status === "open" && (
                <button className="danger-button compact" type="button" onClick={() => close(period.id, period.name)}>
                  Close
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
      <section className="upload-panel">
        <h2>New period</h2>
        <form className="upload-form" onSubmit={create}>
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
          </label>
          <label>
            Start
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} required />
          </label>
          <label>
            End
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} required />
          </label>
          <button className="primary-button">Create period</button>
        </form>
      </section>
    </>
  );
}

function RangeForm({
  start,
  end,
  onStart,
  onEnd,
  singleDate,
}: {
  start: string;
  end: string;
  onStart: (value: string) => void;
  onEnd: (value: string) => void;
  singleDate?: boolean;
}) {
  return (
    <div className="upload-form">
      {!singleDate && (
        <label>
          From
          <input type="date" value={start} onChange={(e) => onStart(e.target.value)} />
        </label>
      )}
      <label>
        {singleDate ? "As of" : "To"}
        <input type="date" value={end} onChange={(e) => onEnd(e.target.value)} />
      </label>
    </div>
  );
}

export function TrialBalanceView() {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [data, setData] = useState<TrialBalance | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    setData(null);
    const query = new URLSearchParams();
    if (start) query.set("start", start);
    if (end) query.set("end", end);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    callAccounting<TrialBalance>(`/trial-balance${suffix}`)
      .then((value) => active && setData(value))
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, [start, end]);

  return (
    <>
      <section className="upload-panel">
        <RangeForm start={start} end={end} onStart={setStart} onEnd={setEnd} />
        <p className="small muted">Posted entries only, by posting date.</p>
      </section>
      {message && <p className="form-error" role="alert">{message}</p>}
      {data && (
        <>
          <p className={data.is_balanced ? "hint" : "form-error"} role="status">
            {data.is_balanced ? "Debits equal credits." : "Debits do not equal credits. Investigate before reporting."}
          </p>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Code</th>
                  <th scope="col">Account</th>
                  <th scope="col" className="num">Debit</th>
                  <th scope="col" className="num">Credit</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.code}>
                    <td>{row.code}</td>
                    <td>{row.name}</td>
                    <td className="num">{money(row.debit_balance)}</td>
                    <td className="num">{money(row.credit_balance)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row" colSpan={2}>Total</th>
                  <td className="num">{money(data.total_debit)}</td>
                  <td className="num">{money(data.total_credit)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </>
      )}
    </>
  );
}

export function ProfitAndLossView() {
  const [start, setStart] = useState(todayIso().slice(0, 8) + "01");
  const [end, setEnd] = useState(todayIso());
  const [data, setData] = useState<ProfitAndLoss | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    setData(null);
    callAccounting<ProfitAndLoss>(`/reports/profit-and-loss?start=${start}&end=${end}`)
      .then((value) => active && setData(value))
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, [start, end]);

  return (
    <>
      <section className="upload-panel">
        <RangeForm start={start} end={end} onStart={setStart} onEnd={setEnd} />
      </section>
      {message && <p className="form-error" role="alert">{message}</p>}
      {data && (
        <section className="settings-card">
          <h2>Profit and loss</h2>
          <p>Income: {money(data.income)}</p>
          <p>Expenses: {money(data.expenses)}</p>
          <p>
            <strong>Net profit: {money(data.net_profit)}</strong>
          </p>
          <p className="small muted">Posted entries between the two dates. No forecasting or advice is included.</p>
        </section>
      )}
    </>
  );
}

export function BalanceSheetView() {
  const [asOf, setAsOf] = useState(todayIso());
  const [data, setData] = useState<BalanceSheet | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    setData(null);
    callAccounting<BalanceSheet>(`/reports/balance-sheet?as_of=${asOf}`)
      .then((value) => active && setData(value))
      .catch((error: Error) => active && setMessage(error.message));
    return () => {
      active = false;
    };
  }, [asOf]);

  return (
    <>
      <section className="upload-panel">
        <RangeForm start="" end={asOf} onStart={() => undefined} onEnd={setAsOf} singleDate />
      </section>
      {message && <p className="form-error" role="alert">{message}</p>}
      {data && (
        <section className="settings-card">
          <h2>Balance sheet</h2>
          <p>Assets: {money(data.assets)}</p>
          <p>Liabilities: {money(data.liabilities)}</p>
          <p>Equity: {money(data.equity)}</p>
          <p>Current earnings: {money(data.current_earnings)}</p>
          <p>
            <strong>Liabilities + equity + earnings: {money(data.liabilities_and_equity)}</strong>
          </p>
          <p className={data.is_balanced ? "hint" : "form-error"} role="status">
            {data.is_balanced ? "Assets equal liabilities plus equity." : "Equation does not hold. Investigate before reporting."}
          </p>
        </section>
      )}
    </>
  );
}
