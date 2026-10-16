export function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const KIND_LABELS: Record<string, string> = {
  bill: "Bill",
  invoice: "Invoice",
  receipt: "Receipt",
  purchase_order: "Purchase order",
  bank_statement: "Bank statement",
  salary: "Salary document",
  emi: "EMI document",
  interest: "Interest document",
  other: "Other",
};

export const DOCUMENT_STATUS_LABELS: Record<string, string> = {
  pending: "Scanning",
  active: "Available",
  quarantined: "Quarantined",
  revoked: "Removed",
};

export const SCAN_STATUS_LABELS: Record<string, string> = {
  pending: "Waiting for scan",
  scanning: "Scanning",
  clean: "Clean",
  infected: "Infected: not stored",
  failed: "Scan failed: not stored",
};
