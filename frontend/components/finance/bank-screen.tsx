"use client";

import { useState } from "react";
import { money, todayIso, type BankAccountRow, type BankTxn } from "./shared";
import { Cards, FinanceFrame, Line, Loading, Notice, Section, useFinance } from "./views";

// Read-only. This screen shows bank accounts and imported transactions. It never creates transfers, payments,
// beneficiaries or bank-setting changes. No bank provider is connected in this build.
export function BankScreen() {
  const accounts = useFinance<BankAccountRow[]>("/bank/accounts");
  const [selected, setSelected] = useState<string | null>(null);
  const monthStart = todayIso().slice(0, 8) + "01";
  const txns = useFinance<BankTxn[]>(selected ? `/bank/accounts/${selected}/transactions?since=${monthStart}&until=${todayIso()}` : null);

  return (
    <FinanceFrame title="Bank (read-only)">
      <Notice kind="warning">Read-only view. Logu does not move money or change bank settings.</Notice>
      <Loading error={accounts.error} loading={accounts.loading} />
      <Section title="Bank accounts">
        <Cards<BankAccountRow> rows={accounts.data ?? []} empty="No bank account is connected. Connect a read-only provider to see balances here."
          render={(a) => (
            <>
              <Line left={<strong>{a.display_name}</strong>} right={money(a.balance)} />
              <span className="fin-muted">{a.provider} · {a.currency}</span>
              <div className="fin-actions">
                <button className="fin-button" onClick={() => setSelected(a.id)}>Show this month</button>
              </div>
            </>
          )} />
      </Section>
      {selected && (
        <Section title="Transactions this month">
          <Loading error={txns.error} loading={txns.loading} />
          <Cards<BankTxn> rows={txns.data ?? []} empty="No transactions imported for this period."
            render={(t) => (
              <Line left={<span>{t.date} · {t.description}</span>}
                right={<span>{t.direction === "credit" ? "+" : "−"} {money(t.amount.replace("-", ""))}</span>} />
            )} />
        </Section>
      )}
      {!selected && !accounts.loading && (accounts.data?.length ?? 0) === 0 && (
        <Notice kind="warning">No provider is connected, so there is nothing to import.</Notice>
      )}
    </FinanceFrame>
  );
}
