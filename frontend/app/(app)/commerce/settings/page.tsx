import { PageHeader } from "../../../../components/page-header";
import { MappingsView } from "../../../../components/commerce/catalogue-views";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Business" title="Accounting mapping" />
      <MappingsView />
    </>
  );
}
