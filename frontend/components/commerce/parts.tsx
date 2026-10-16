"use client";

import { STATUS_LABELS, statusClass } from "./shared";

export function StatusPill({ status }: { status: string }) {
  return <span className={`status-pill ${statusClass(status)}`}>{STATUS_LABELS[status] ?? status}</span>;
}

export function Notice({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p className="form-error" role="alert">
      {message}
    </p>
  );
}
