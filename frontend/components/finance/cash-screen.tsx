"use client";

import { money, type Forecast } from "./shared";
import { Badge, Cards, FinanceFrame, Line, Loading, Metric, Section, useFinance } from "./views";

export function CashScreen() {
  const position = useFinance<{
    ledger_cash_total: string;
    ledger_accounts: { code: string; name: string; balance: string }[];
    bank_balance_total: string | null;
    bank_accounts: { name: string; balance: string | null; as_of: string | null }[];
  }>("/cash-position");
  const forecast = useFinance<Forecast>("/forecast?weeks=8");
  return (
    <FinanceFrame title="Cash position">
      <Loading error={position.error} loading={position.loading} />
      {position.data && (
        <>
          <div className="fin-metrics">
            <Metric label="Ledger cash" value={money(position.data.ledger_cash_total)} />
            <Metric label="Bank balance" value={money(position.data.bank_balance_total)} />
          </div>
          <Section title="Ledger cash and bank accounts">
            <Cards rows={position.data.ledger_accounts} empty="No cash or bank accounts in the ledger."
              render={(a) => <Line left={`${a.code} ${a.name}`} right={money(a.balance)} />} />
          </Section>
          <Section title="Bank balances (provider, read-only)">
            <Cards rows={position.data.bank_accounts} empty="No bank connected. Balances appear only when a provider supplies them."
              render={(b) => <Line left={b.name} right={money(b.balance)} />} />
          </Section>
        </>
      )}
      <Loading error={forecast.error} loading={forecast.loading} />
      {forecast.data && (
        <Section title="Forecast">
          <Cards rows={forecast.data.weeks} empty="No forecast."
            render={(w) => (
              <Line left={`Week ${w.week} to ${w.week_end}`}
                right={<span>{w.negative ? <Badge tone="error">Shortfall</Badge> : null} {money(w.closing)}</span>} />
            )} />
        </Section>
      )}
    </FinanceFrame>
  );
}
