import { PageHeader } from "../../../../../components/page-header";
import { MovementsView } from "../../../../../components/commerce/catalogue-views";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Inventory" title="Stock movements" />
      <MovementsView />
    </>
  );
}
