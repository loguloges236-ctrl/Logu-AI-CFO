import { PageHeader } from "../../../../../components/page-header";
import { AccountDetailView } from "../../../../../components/accounting/accounts-view";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <PageHeader eyebrow="Account details" title="Account" />
      <AccountDetailView accountId={id} />
    </>
  );
}
