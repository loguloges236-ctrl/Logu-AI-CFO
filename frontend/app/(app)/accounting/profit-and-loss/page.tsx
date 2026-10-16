import { PageHeader } from "../../../../components/page-header";
import { ProfitAndLossView } from "../../../../components/accounting/reports-view";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Profit and loss" title="Profit and loss" />
      <ProfitAndLossView />
    </>
  );
}
