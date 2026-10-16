import { PageHeader } from "../../../../components/page-header";
import { TrialBalanceView } from "../../../../components/accounting/reports-view";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Trial balance" title="Trial balance" />
      <TrialBalanceView />
    </>
  );
}
