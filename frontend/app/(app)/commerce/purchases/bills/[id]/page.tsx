import { PageHeader } from "../../../../../../components/page-header";
import { PurchaseBillDetailView } from "../../../../../../components/commerce/trade-views";

export default async function Page({ params }: { params: Promise<{ billId: string }> }) {
  const { billId } = await params;
  return (
    <>
      <PageHeader eyebrow="Purchase bill" title="Purchase bill" />
      <PurchaseBillDetailView billId={billId} />
    </>
  );
}
