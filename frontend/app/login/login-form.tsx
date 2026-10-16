"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { apiFetch, errorMessage } from "../../lib/api";

export function LoginForm({ sessionExpired }: { sessionExpired: boolean }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const response = await apiFetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (response.ok) {
        setPassword("");
        router.replace("/");
        router.refresh();
        return;
      }
      setMessage(await errorMessage(response));
    } catch {
      setMessage("Sign-in could not be completed. Check your connection and try again.");
    } finally {
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <form className="login-card" onSubmit={submit} noValidate={false}>
      <p className="eyebrow">Logu AI CFO</p>
      <h1>Sign in</h1>
      {sessionExpired && <p className="hint" role="status">Your session ended. Please sign in again.</p>}
      <p className="muted">Use your owner password. Voice unlock is not available yet.</p>
      <label>
        Email
        <input
          autoComplete="username"
          type="email"
          inputMode="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
          maxLength={320}
        />
      </label>
      <label>
        Password
        <input
          autoComplete="current-password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
          maxLength={256}
        />
      </label>
      <button className="primary-button" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      {message && (
        <p className="form-error" role="alert">
          {message}
        </p>
      )}
    </form>
  );
}
