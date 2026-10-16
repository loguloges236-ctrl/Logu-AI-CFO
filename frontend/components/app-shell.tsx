import Link from "next/link";
import type { ReactNode } from "react";
import { LogoutButton } from "./logout-button";

const items = [
  { href: "/", label: "Overview", icon: "⌂" },
  { href: "/documents", label: "Documents", icon: "▤" },
  { href: "/accounting", label: "Accounting", icon: "₹" },
  { href: "/finance", label: "Finance", icon: "◎" },
  { href: "/commerce", label: "Business", icon: "▦" },
  { href: "/settings/security", label: "Security", icon: "◈" },
];

export function AppShell({ email, children }: { email: string; children: ReactNode }) {
  return (
    <div className="app-shell">
      <header className="top-bar">
        <Link href="/" className="brand">
          <span className="brand-mark" aria-hidden>
            L
          </span>
          Logu AI CFO
        </Link>
        <nav className="desktop-nav" aria-label="Primary navigation">
          {items.map((item) => (
            <Link key={item.href} href={item.href}>
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="account">
          <span className="muted small" title={email}>
            {email}
          </span>
          <LogoutButton />
        </div>
      </header>
      <main id="main" className="content">
        {children}
      </main>
      <nav className="bottom-nav" aria-label="Mobile navigation">
        {items.map((item) => (
          <Link key={item.href} href={item.href}>
            <span aria-hidden>{item.icon}</span>
            {item.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
