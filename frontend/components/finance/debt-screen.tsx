"use client";

import { useState, type FormEvent } from "react";
import { callFinance, jsonPost, money, todayIso, type DebtRow } from "./shared";
import { Badge, Cards, FinanceFrame, Line, Loading, Metric, Notice, Section, useFinance } from "./views";

export function DebtScreen() {
  const debts = useFinance<DebtRow[]>("/debts");
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [lender, setLender] = useState("");
  const [principal, setPrincipal] = useState("");
  const [rate, setRate] = useState("");
  const [tenure, setTenure] = useState("12");
  const [startDate, setStartDate] = useState(todayIso());

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await callFinance("/debts", jsonPost({
        name, lender, principal, annual_rate: rate, tenure_months: Number(tenure), start_date: startDate,
        interest_type: "reducing",
      }));
      setMsg({ kind: "ok", text: "Debt created with its EMI schedule and EMI obligation." });
      setName(""); setLender(""); setPrincipal(""); setRate("");
      debts.reload();
    } catch (failure) {
      setMsg({ kind: "error", text: failure instanceof Error ? failure.message : "Debt could not be created." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <FinanceFrame title="Debts">
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <div className="fin-metrics">
        <Metric label="Debts" value={String(debts.data?.length ?? 0)} />
      </div>
      <Loading error={debts.error} loading={debts.loading} />
      <Section title="Active debts">
        <Cards<DebtRow> rows={debts.data ?? []} empty="No debts recorded."
          render={(d) => (
            <>
              <Line left={<strong>{d.name}</strong>} right={money(d.outstanding)} />
              <Line left={<span className="fin-muted">{d.lender} · {d.annual_rate}% p.a.</span>}
                right={<span className="fin-muted">EMI {money(d.emi)}</span>} />
              <Line left={<span className="fin-muted">{d.installments_paid_by_schedule} of {d.installments} paid</span>}
                right={<span className="fin-muted">next {d.next_due_date ?? "—"}</span>} />
              <Badge tone={d.status === "active" ? "ok" : "warning"}>{d.status}</Badge>
            </>
          )} />
      </Section>
      <Section title="Add a debt">
        <form className="fin-form" onSubmit={create}>
          <label>Name <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} /></label>
          <label>Lender <input value={lender} onChange={(e) => setLender(e.target.value)} required maxLength={120} /></label>
          <label>Principal (₹) <input inputMode="decimal" value={principal} onChange={(e) => setPrincipal(e.target.value)} required /></label>
          <label>Annual rate (%) <input inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} required /></label>
          <label>Tenure (months) <input inputMode="numeric" value={tenure} onChange={(e) => setTenure(e.target.value)} required /></label>
          <label>Start date <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} required /></label>
          <div className="fin-actions">
            <button className="fin-button primary" type="submit" disabled={busy}>Create debt</button>
          </div>
        </form>
      </Section>
    </FinanceFrame>
  );
}
