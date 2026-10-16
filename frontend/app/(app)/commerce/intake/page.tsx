import { PageHeader } from "../../../../components/page-header";
import { IntakeView } from "../../../../components/commerce/trade-views";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Document intake" title="Upload and processing" />
      <IntakeView />
    </>
  );
}
