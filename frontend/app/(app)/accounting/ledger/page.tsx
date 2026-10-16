import { PageHeader } from "../../../../components/page-header";
import { LedgerPageView } from "../../../../components/accounting/accounts-view";

export default async function Page({ searchParams }: { searchParams: Promise<{ account?: string; start?: string; end?: string }> }) {
  const query = await searchParams;
  return (
    <>
      <PageHeader eyebrow="General ledger" title="General ledger">
        <p className="muted">Posted movements for one account, with running balance.</p>
      </PageHeader>
      <LedgerPageView accountId={query.account ?? ""} start={query.start ?? ""} end={query.end ?? ""} />
    </>
  );
}
