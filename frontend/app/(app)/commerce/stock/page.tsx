import { PageHeader } from "../../../../components/page-header";
import { StockView } from "../../../../components/commerce/catalogue-views";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Inventory" title="Stock" />
      <StockView />
    </>
  );
}
