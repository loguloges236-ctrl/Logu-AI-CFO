import { PageHeader } from "../../../../components/page-header";
import { SecurityPanel } from "./security-panel";

export default function SecuritySettingsPage() {
  return (
    <>
      <PageHeader eyebrow="Owner protection" title="Security" />
      <SecurityPanel />
    </>
  );
}
