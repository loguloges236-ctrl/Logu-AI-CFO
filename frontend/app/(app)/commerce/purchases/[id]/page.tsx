import { PageHeader } from "../../../../../components/page-header";
import { PurchaseDetailView } from "../../../../../components/commerce/trade-views";

export default async function Page({ params }: { params: Promise<{ orderId: string }> }) {
  const { orderId } = await params;
  return (
    <>
      <PageHeader eyebrow="Purchase order" title="Purchase order" />
      <PurchaseDetailView orderId={orderId} />
    </>
  );
}
