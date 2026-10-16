import { PageHeader } from "../../../../../components/page-header";
import { JournalDetailView } from "../../../../../components/accounting/journal-view";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <PageHeader eyebrow="Journal entry" title="Journal entry" />
      <JournalDetailView entryId={id} />
    </>
  );
}
