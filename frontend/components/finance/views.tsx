"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { callFinance, money } from "./shared";
import "./finance.css";

export const FINANCE_PAGES: { href: string; label: string }[] = [
  { href: "/finance", label: "Dashboard" },
  { href: "/finance/cash", label: "Cash" },
  { href: "/finance/upcoming", label: "Upcoming" },
  { href: "/finance/debts", label: "Debts" },
  { href: "/finance/emi", label: "EMI" },
  { href: "/finance/interest", label: "Interest" },
  { href: "/finance/salaries", label: "Salaries" },
  { href: "/finance/recurring", label: "Recurring" },
  { href: "/finance/receivables", label: "Receivables" },
  { href: "/finance/payables", label: "Payables" },
  { href: "/finance/settlements", label: "Settlements" },
  { href: "/finance/bank", label: "Bank" },
  { href: "/finance/reconciliation", label: "Reconciliation" },
  { href: "/finance/alerts", label: "Alerts" },
];

export function FinanceFrame({ title, children }: { title: string; children: ReactNode }) {
  const pathname = usePathname();
  return (
    <main className="fin-page">
      <h1 className="fin-title">{title}</h1>
      <nav className="fin-nav" aria-label="Finance sections">
        {FINANCE_PAGES.map((page) => (
          <Link key={page.href} href={page.href} aria-current={pathname === page.href ? "page" : undefined}>
            {page.label}
          </Link>
        ))}
      </nav>
      {children}
    </main>
  );
}

// Loads one finance endpoint. `reload` re-fetches after a write. Errors are the server's safe messages only.
export function useFinance<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (path === null) {
      return;
    }
    let active = true;
    setLoading(true);
    callFinance<T>(path)
      .then((value) => {
        if (active) {
          setData(value);
          setError(null);
        }
      })
      .catch((failure: unknown) => {
        if (active) {
          setError(failure instanceof Error ? failure.message : "Could not load this section.");
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [path, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, error, loading, reload };
}

export function Notice({ kind = "warning", children }: { kind?: "warning" | "error" | "ok"; children: ReactNode }) {
  return (
    <div className={`fin-notice ${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function Loading({ error, loading }: { error: string | null; loading: boolean }) {
  if (error) {
    return <Notice kind="error">{error}</Notice>;
  }
  if (loading) {
    return <p className="fin-muted">Loading…</p>;
  }
  return null;
}

export function Metric({ label, value, tone }: { label: string; value: string; tone?: "warning" | "error" }) {
  return (
    <div className="fin-metric">
      <div className="label">{label}</div>
      <div className="value" style={tone === "error" ? { color: "#b42318" } : undefined}>{value}</div>
    </div>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="fin-section">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

export function Badge({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={`fin-badge ${tone}`}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="fin-muted">{children}</p>;
}

// Stacked cards: one record per block so the list reads on a phone without horizontal scrolling.
export function Cards<T>({ rows, empty, render }: { rows: T[]; empty: string; render: (row: T) => ReactNode }) {
  if (rows.length === 0) {
    return <Empty>{empty}</Empty>;
  }
  return (
    <div>
      {rows.map((row, index) => (
        <div className="fin-card" key={(row as { id?: string; key?: string }).id ?? (row as { key?: string }).key ?? index}>
          {render(row)}
        </div>
      ))}
    </div>
  );
}

export function Line({ left, right }: { left: ReactNode; right: ReactNode }) {
  return (
    <div className="row">
      <span>{left}</span>
      <span>{right}</span>
    </div>
  );
}

export function Money({ value }: { value: string | null | undefined }) {
  return <>{money(value)}</>;
}

export function bucketTone(bucket: string): string {
  if (bucket === "overdue") return "overdue";
  if (bucket === "today" || bucket === "tomorrow" || bucket === "this_week") return "warning";
  return "ok";
}

export function bucketLabel(bucket: string): string {
  const labels: Record<string, string> = {
    overdue: "Overdue", today: "Due today", tomorrow: "Due tomorrow", this_week: "This week", later: "Later",
  };
  return labels[bucket] ?? bucket;
}
