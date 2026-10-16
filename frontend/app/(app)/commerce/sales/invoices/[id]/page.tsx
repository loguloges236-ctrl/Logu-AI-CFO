import { PageHeader } from "../../../../../../components/page-header";
import { SalesInvoiceDetailView } from "../../../../../../components/commerce/trade-views";

export default async function Page({ params }: { params: Promise<{ invoiceId: string }> }) {
  const { invoiceId } = await params;
  return (
    <>
      <PageHeader eyebrow="Sales invoice" title="Sales invoice" />
      <SalesInvoiceDetailView invoiceId={invoiceId} />
    </>
  );
}
