"use client";

import { useEffect, useState } from "react";
import { callAccounting, dateText, type Account, type DueRow, type Obligation } from "./shared";
import { Badge, Cards, FinanceFrame, Line, Loading, Money, Section, bucketLabel, bucketTone, useFinance } from "./views";
import { PaymentForm } from "./payment-form";

// Shared screen for EMI, interest, salary, recurring and upcoming items. Each row shows the next unpaid period.
export function ObligationScreen({ title, path, kinds }: { title: string; path: string; kinds?: string[] }) {
  const { data, error, loading, reload } = useFinance<Obligation[]>(path);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  useEffect(() => {
    callAccounting<Account[]>("/accounts").then(setAccounts).catch(() => setAccounts([]));
  }, []);

  const rows = (data ?? []).filter((o) => !kinds || kinds.includes(o.kind));
  return (
    <FinanceFrame title={title}>
      <Loading error={error} loading={loading} />
      <Section title={`${rows.length} active`}>
        <Cards<Obligation>
          rows={rows}
          empty="Nothing is recorded here yet."
          render={(o) => (
            <>
              <Line left={<strong>{o.title}</strong>} right={<Money value={o.amount} />} />
              <Line left={<span className="fin-muted">{o.counterparty ?? o.kind} · {o.frequency}</span>}
                right={<span className="fin-muted">first due {o.first_due_date}</span>} />
              {o.is_critical && <Badge tone="critical">Critical</Badge>}
              <div className="fin-actions">
                <button className="fin-button" onClick={() => setOpenId(openId === o.id ? null : o.id)}>
                  {openId === o.id ? "Close" : "Record payment"}
                </button>
              </div>
              {openId === o.id && (
                <PaymentForm obligation={o} accounts={accounts} duePeriod={o.first_due_date}
                  onDone={() => { setOpenId(null); reload(); }} />
              )}
            </>
          )}
        />
      </Section>
    </FinanceFrame>
  );
}

// Upcoming: unpaid periods from the lookback window to the horizon, with overdue first.
export function UpcomingScreen() {
  const { data, error, loading } = useFinance<DueRow[]>("/upcoming");
  return (
    <FinanceFrame title="Upcoming obligations">
      <Loading error={error} loading={loading} />
      <Section title="Unpaid periods">
        <Cards<DueRow>
          rows={data ?? []}
          empty="No unpaid obligations in the window."
          render={(r) => (
            <>
              <Line left={<strong>{r.title}</strong>} right={<Money value={r.amount} />} />
              <Line left={<span className="fin-muted">{r.kind} · due {dateText(r.due_date)}</span>}
                right={<Badge tone={bucketTone(r.bucket)}>{bucketLabel(r.bucket)}</Badge>} />
            </>
          )}
        />
      </Section>
    </FinanceFrame>
  );
}

