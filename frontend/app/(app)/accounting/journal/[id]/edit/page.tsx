import { PageHeader } from "../../../../../../components/page-header";
import { JournalFormView } from "../../../../../../components/accounting/journal-view";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <PageHeader eyebrow="Edit draft" title="Edit draft entry" />
      <JournalFormView entryId={id} />
    </>
  );
}
