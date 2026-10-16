import { PageHeader } from "../../../../components/page-header";
import { PurchasesView } from "../../../../components/commerce/trade-views";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Purchasing" title="Purchase documents" />
      <PurchasesView />
    </>
  );
}
