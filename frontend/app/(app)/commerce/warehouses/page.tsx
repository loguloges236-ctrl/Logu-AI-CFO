import { PageHeader } from "../../../../components/page-header";
import { WarehousesView } from "../../../../components/commerce/catalogue-views";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Inventory" title="Warehouses and locations" />
      <WarehousesView />
    </>
  );
}
