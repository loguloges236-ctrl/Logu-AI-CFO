"use client";

// Browser-side API client. Requests are same-origin (/api/v1 is routed by the edge proxy), so the
// HttpOnly session cookie is sent automatically and no API origin is embedded in the bundle.

const API_PREFIX = "/api/v1";
const STATE_CHANGING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)logu_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1] ?? "") : "";
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (STATE_CHANGING.has(method)) {
    headers.set("X-CSRF-Token", csrfToken());
  }
  const response = await fetch(API_PREFIX + path, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (response.status === 401 && !window.location.pathname.startsWith("/login")) {
    window.location.assign("/login?expired=1");
  }
  return response;
}

const FALLBACK_MESSAGE = "The request could not be completed. Please try again.";

// Returns only the server's safe, human-readable message. Raw bodies and stack traces are never shown.
export async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { error?: { message?: unknown } };
    const message = payload.error?.message;
    return typeof message === "string" ? message : FALLBACK_MESSAGE;
  } catch {
    return FALLBACK_MESSAGE;
  }
}

export function downloadUrl(documentId: string, fileId: string): string {
  return `${API_PREFIX}/documents/${encodeURIComponent(documentId)}/files/${encodeURIComponent(fileId)}`;
}
