import { PageHeader } from "../../../components/page-header";
import { DocumentManager } from "./document-manager";

export default function DocumentsPage() {
  return (
    <>
      <PageHeader eyebrow="Private document vault" title="Documents">
        <p className="muted">PDF, JPG, PNG or WEBP up to 25 MB. Files are scanned for malware and stored encrypted.</p>
      </PageHeader>
      <DocumentManager />
    </>
  );
}
