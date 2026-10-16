import { PageHeader } from "../../../components/page-header";
import { DashboardView } from "../../../components/accounting/reports-view";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Core accounting" title="Accounting" />
      <DashboardView />
    </>
  );
}
