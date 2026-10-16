"use client";

import { money, type DocumentSummary } from "./shared";
import { Badge, Cards, FinanceFrame, Line, Loading, Metric, Section, useFinance } from "./views";

export function DocumentsScreen({ direction }: { direction: "receivables" | "payables" }) {
  const { data, error, loading } = useFinance<DocumentSummary>(`/${direction}`);
  const title = direction === "receivables" ? "Receivables" : "Payables";
  return (
    <FinanceFrame title={title}>
      <Loading error={error} loading={loading} />
      {data && (
        <>
          <div className="fin-metrics">
            <Metric label="Open" value={money(data.open_total)} />
            <Metric label="Overdue" value={money(data.overdue_total)} tone="error" />
            <Metric label="Overdue documents" value={String(data.overdue_count)} />
          </div>
          <Section title="Open documents">
            <Cards rows={data.items} empty="No open posted documents."
              render={(d) => (
                <>
                  <Line left={<strong>{d.number}</strong>} right={money(d.outstanding)} />
                  <Line left={<span className="fin-muted">{d.party} · due {d.due_date ?? "—"}</span>}
                    right={<Badge tone={d.payment_status === "overdue" ? "overdue" : "ok"}>{d.payment_status}</Badge>} />
                  <span className="fin-muted">Total {money(d.total)} · settled {money(d.settled)}</span>
                </>
              )} />
          </Section>
          <p className="fin-muted">Settle these from the Settlements screen. Settlements post to the journal once approved.</p>
        </>
      )}
    </FinanceFrame>
  );
}
