// Voice unlock is NOT implemented. This card states that plainly and offers no action that could
// suggest otherwise. Biometric verification requires an approved provider (see docs/ROADMAP.md).
export function VoiceStatusCard() {
  return (
    <article className="module-card" aria-labelledby="voice-status-title">
      <span className="card-state card-state-muted">Not yet implemented</span>
      <h2 id="voice-status-title">Voice unlock</h2>
      <p>
        Biometric voice verification is not available. Sign in with your password. Speech recognition is never
        used as authentication.
      </p>
    </article>
  );
}
