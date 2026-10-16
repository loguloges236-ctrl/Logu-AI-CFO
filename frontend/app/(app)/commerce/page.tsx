import { PageHeader } from "../../../components/page-header";
import { DashboardView } from "../../../components/commerce/catalogue-views";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Business" title="Dashboard" />
      <DashboardView />
    </>
  );
}
