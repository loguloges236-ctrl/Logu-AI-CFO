"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "../../../../lib/api";
import { formatDateTime } from "../../../../lib/format";
import type { DeviceItem, SessionItem, VoiceStatus } from "../../../../lib/types";

const MIN_PASSWORD_LENGTH = 14;

export function SecurityPanel() {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [devices, setDevices] = useState<DeviceItem[]>([]);
  const [voice, setVoice] = useState<VoiceStatus | null>(null);
  const [notice, setNotice] = useState("");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [sessionResponse, deviceResponse, voiceResponse] = await Promise.all([
      apiFetch("/auth/sessions"),
      apiFetch("/auth/devices"),
      apiFetch("/security/voice-unlock"),
    ]);
    if (sessionResponse.ok) setSessions(((await sessionResponse.json()) as { sessions: SessionItem[] }).sessions);
    if (deviceResponse.ok) setDevices(((await deviceResponse.json()) as { devices: DeviceItem[] }).devices);
    if (voiceResponse.ok) setVoice((await voiceResponse.json()) as VoiceStatus);
  }, []);

  useEffect(() => {
    load().catch(() => setNotice("Security information could not be loaded."));
  }, [load]);

  async function post(path: string, success: string) {
    setBusy(true);
    setNotice("");
    try {
      const response = await apiFetch(path, { method: "POST" });
      if (response.ok) {
        setNotice(success);
        await load();
      } else {
        setNotice(await errorMessage(response));
      }
    } catch {
      setNotice("The request could not be completed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function revokeSession(id: string) {
    setBusy(true);
    setNotice("");
    try {
      const response = await apiFetch(`/auth/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (response.ok) {
        setNotice("Session signed out.");
        await load();
      } else {
        setNotice(await errorMessage(response));
      }
    } finally {
      setBusy(false);
    }
  }

  async function revokeDevice(id: string) {
    setBusy(true);
    setNotice("");
    try {
      const response = await apiFetch(`/auth/devices/${encodeURIComponent(id)}/revoke`, { method: "POST" });
      if (response.ok) {
        setNotice("Device revoked. Its sessions were signed out.");
        await load();
      } else {
        setNotice(await errorMessage(response));
      }
    } finally {
      setBusy(false);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (next.length < MIN_PASSWORD_LENGTH) {
      setNotice(`The new password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (next !== confirmation) {
      setNotice("The new passwords do not match.");
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const response = await apiFetch("/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      if (response.ok) {
        setNotice("Password changed. Other sessions were signed out.");
        setCurrent("");
        setNext("");
        setConfirmation("");
        await load();
      } else {
        setNotice(await errorMessage(response));
      }
    } catch {
      setNotice("The password could not be changed. Try again.");
    } finally {
      setCurrent("");
      setNext("");
      setConfirmation("");
      setBusy(false);
    }
  }

  return (
    <>
      {notice && (
        <p className="hint" role="status">
          {notice}
        </p>
      )}

      <section className="settings-card" aria-labelledby="voice-title">
        <h2 id="voice-title">Voice unlock</h2>
        <p>
          {voice?.status_message ??
            "Voice unlock is not yet implemented. Use your password."}
        </p>
      </section>

      <section className="settings-card" aria-labelledby="sessions-title">
        <h2 id="sessions-title">Active sessions</h2>
        <ul className="plain-list">
          {sessions.map((session) => (
            <li key={session.id} className="row-item">
              <div>
                <strong>{session.current ? "This session" : "Other session"}</strong>
                <p className="muted small">
                  Last active {formatDateTime(session.last_seen_at)} · expires {formatDateTime(session.expires_at)}
                </p>
              </div>
              {!session.current && (
                <button className="danger-button compact" type="button" disabled={busy} onClick={() => revokeSession(session.id)}>
                  Sign out
                </button>
              )}
            </li>
          ))}
        </ul>
        <button className="secondary-button" type="button" disabled={busy} onClick={() => post("/auth/sessions/revoke-others", "Other sessions signed out.")}>
          Sign out all other sessions
        </button>
      </section>

      <section className="settings-card" aria-labelledby="devices-title">
        <h2 id="devices-title">Devices</h2>
        <ul className="plain-list">
          {devices.map((device) => (
            <li key={device.id} className="row-item">
              <div>
                <strong>{device.current ? "This device" : "Other device"}</strong>
                <p className="muted small">
                  {device.revoked ? "Revoked" : `Last seen ${formatDateTime(device.last_seen_at)}`}
                </p>
              </div>
              {!device.revoked && (
                <button className="danger-button compact" type="button" disabled={busy} onClick={() => revokeDevice(device.id)}>
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="settings-card" aria-labelledby="password-title">
        <h2 id="password-title">Change password</h2>
        <form className="upload-form" onSubmit={changePassword}>
          <label>
            Current password
            <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required maxLength={256} />
          </label>
          <label>
            New password (at least {MIN_PASSWORD_LENGTH} characters)
            <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={MIN_PASSWORD_LENGTH} maxLength={256} />
          </label>
          <label>
            Repeat new password
            <input type="password" autoComplete="new-password" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} required maxLength={256} />
          </label>
          <button className="primary-button" disabled={busy}>
            Change password
          </button>
        </form>
      </section>
    </>
  );
}
