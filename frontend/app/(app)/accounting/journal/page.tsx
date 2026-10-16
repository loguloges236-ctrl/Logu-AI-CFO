import { PageHeader } from "../../../../components/page-header";
import { JournalListView } from "../../../../components/accounting/journal-view";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Journal entries" title="Journal" />
      <JournalListView />
    </>
  );
}
