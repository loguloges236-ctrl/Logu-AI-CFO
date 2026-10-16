import { PageHeader } from "../../../../components/page-header";
import { SalesView } from "../../../../components/commerce/trade-views";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Sales" title="Sales documents" />
      <SalesView />
    </>
  );
}
