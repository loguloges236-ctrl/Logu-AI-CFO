import { PageHeader } from "../../../../../components/page-header";
import { JournalFormView } from "../../../../../components/accounting/journal-view";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="New journal entry" title="New journal entry" />
      <JournalFormView />
    </>
  );
}
