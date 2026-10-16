import { cookies } from "next/headers";
import type { Owner } from "./types";

// Server-side session check used by protected layouts. The API is the only authority: the session
// cookie is forwarded to it and the result decides whether a page renders. Cookie presence alone is
// never sufficient. The API base is read at runtime from a server-only variable.
const SESSION_COOKIE = "logu_session";

export async function getOwner(): Promise<Owner | null> {
  const jar = await cookies();
  const session = jar.get(SESSION_COOKIE);
  if (!session) return null;
  const base = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  try {
    const response = await fetch(`${base}/api/v1/auth/me`, {
      headers: { cookie: `${SESSION_COOKIE}=${session.value}` },
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as Owner;
  } catch {
    return null;
  }
}
