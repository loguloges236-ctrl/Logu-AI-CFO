import { redirect } from "next/navigation";
import { AppShell } from "../../components/app-shell";
import { getOwner } from "../../lib/server-session";

// Authentication guard for every page in this group. Runs on the server before any protected content
// is produced, so unauthenticated visitors never receive application pages or data.
export default async function ProtectedLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const owner = await getOwner();
  if (!owner) {
    redirect("/login");
  }
  return <AppShell email={owner.email}>{children}</AppShell>;
}
