import { PageHeader } from "../../../../../components/page-header";
import { IntakeDetailView } from "../../../../../components/commerce/trade-views";

export default async function Page({ params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  return (
    <>
      <PageHeader eyebrow="Document review" title="Review extracted document" />
      <IntakeDetailView jobId={jobId} />
    </>
  );
}
