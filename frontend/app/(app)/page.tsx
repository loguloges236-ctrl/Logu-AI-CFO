import Link from "next/link";
import { PageHeader } from "../../components/page-header";
import { VoiceStatusCard } from "../../components/voice-status-card";

// Phase 2 modules are listed for orientation only. No figures are shown because no financial
// calculation service exists yet; showing numbers here would be fabricated.
const upcoming = [
  "Cash position",
  "Receivables",
  "Payables",
  "Inventory",
  "EMI and debt",
  "Salary",
  "Interest",
  "AI insights",
];

export default function OverviewPage() {
  return (
    <>
      <PageHeader eyebrow="Private control center" title="Overview">
        <p className="lead">Secure access and document intake are available. Financial modules arrive in Phase 2.</p>
      </PageHeader>

      <section className="card-grid" aria-label="Available now">
        <article className="module-card action-card">
          <span className="card-state">Available now</span>
          <h2>Documents</h2>
          <p>Upload bills, invoices and receipts. Every file is scanned before it can be stored or opened.</p>
          <Link href="/documents">Open documents →</Link>
        </article>
        <article className="module-card">
          <span className="card-state">Available now</span>
          <h2>Security</h2>
          <p>Review active sessions and devices, change your password, and sign out other places.</p>
          <Link href="/settings/security">Open security →</Link>
        </article>
        <VoiceStatusCard />
      </section>

      <section className="card-grid" aria-label="Planned modules">
        {upcoming.map((title) => (
          <article className="module-card" key={title}>
            <span className="card-state">Phase 2</span>
            <h2>{title}</h2>
            <p>Not yet available.</p>
          </article>
        ))}
      </section>
    </>
  );
}
