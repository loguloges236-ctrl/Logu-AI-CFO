"use client";

import { money, type Dashboard } from "./shared";
import { Badge, Cards, FinanceFrame, Line, Loading, Metric, Money, Notice, Section, bucketLabel, bucketTone, useFinance } from "./views";

export function DashboardScreen() {
  const { data, error, loading } = useFinance<Dashboard>("/dashboard");
  return (
    <FinanceFrame title="Finance overview">
      <Loading error={error} loading={loading} />
      {data && (
        <>
          {data.alerts.map((alert) => (
            <Notice key={alert.code} kind={alert.severity === "critical" ? "error" : "warning"}>{alert.message}</Notice>
          ))}
          <div className="fin-metrics">
            <Metric label="Cash available" value={money(data.cash_available)} />
            <Metric label="Bank balance" value={money(data.bank_balance)} />
            <Metric label="Receivables open" value={money(data.receivables_open)} />
            <Metric label="Payables open" value={money(data.payables_open)} />
            <Metric label="Overdue receivables" value={money(data.receivables_overdue)} tone="warning" />
            <Metric label="Overdue payables" value={money(data.payables_overdue)} tone="error" />
            <Metric label="Debt outstanding" value={money(data.debt_outstanding)} />
            <Metric label="Monthly obligations" value={money(data.monthly_obligations)} />
            <Metric label="Monthly income" value={money(data.monthly_income)} />
            <Metric label="Monthly expenses" value={money(data.monthly_expenses)} />
            <Metric label="Net cash flow" value={money(data.net_cash_flow)} />
            <Metric label="Unreconciled items" value={String(data.unreconciled_count)} />
          </div>
          <Section title="Next 8 weeks">
            <Cards
              rows={data.forecast}
              empty="No forecast."
              render={(w) => (
                <Line left={<span>Week {w.week} (to {w.week_end})</span>}
                  right={<span>{w.negative ? <Badge tone="error">Negative</Badge> : null} {money(w.closing)}</span>} />
              )}
            />
          </Section>
          <Section title="Upcoming obligations">
            <Cards<NonNullable<Dashboard["upcoming"]>[number]>
              rows={data.upcoming}
              empty="Nothing due in the window."
              render={(r) => (
                <>
                  <Line left={<strong>{r.title}</strong>} right={<Money value={r.amount} />} />
                  <Line left={<span className="fin-muted">due {r.due_date}</span>}
                    right={<Badge tone={bucketTone(r.bucket)}>{bucketLabel(r.bucket)}</Badge>} />
                </>
              )}
            />
          </Section>
        </>
      )}
    </FinanceFrame>
  );
}
