"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiFetch } from "../lib/api";

export function LogoutButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function logout() {
    setBusy(true);
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } finally {
      // Navigate even if the request failed: the server-side guard re-checks on the next page load.
      router.replace("/login");
      router.refresh();
    }
  }

  return (
    <button className="secondary-button compact" type="button" onClick={logout} disabled={busy}>
      {busy ? "Signing out…" : "Sign out"}
    </button>
  );
}
