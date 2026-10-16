import { PageHeader } from "../../../../../../components/page-header";
import { SalesOrderDetailView } from "../../../../../../components/commerce/trade-views";

export default async function Page({ params }: { params: Promise<{ orderId: string }> }) {
  const { orderId } = await params;
  return (
    <>
      <PageHeader eyebrow="Sales order" title="Sales order" />
      <SalesOrderDetailView orderId={orderId} />
    </>
  );
}
