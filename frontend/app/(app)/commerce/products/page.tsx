import { PageHeader } from "../../../../components/page-header";
import { ProductsView } from "../../../../components/commerce/catalogue-views";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Catalogue" title="Products and SKUs" />
      <ProductsView />
    </>
  );
}
