import { redirect } from "next/navigation";
import { getOwner } from "../../lib/server-session";
import { LoginForm } from "./login-form";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ expired?: string }>;
}) {
  // Already signed in: go to the application instead of showing the form.
  if (await getOwner()) {
    redirect("/");
  }
  const params = await searchParams;
  return (
    <main className="login-page">
      <LoginForm sessionExpired={params.expired === "1"} />
    </main>
  );
}
