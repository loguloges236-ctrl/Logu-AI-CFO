import { PageHeader } from "../../../../components/page-header";
import { AccountsView } from "../../../../components/accounting/accounts-view";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Chart of accounts" title="Accounts" />
      <AccountsView />
    </>
  );
}
