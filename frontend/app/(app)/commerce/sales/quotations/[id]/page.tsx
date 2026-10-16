import { PageHeader } from "../../../../../../components/page-header";
import { QuotationDetailView } from "../../../../../../components/commerce/trade-views";

export default async function Page({ params }: { params: Promise<{ quotationId: string }> }) {
  const { quotationId } = await params;
  return (
    <>
      <PageHeader eyebrow="Quotation" title="Quotation" />
      <QuotationDetailView quotationId={quotationId} />
    </>
  );
}
