"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { apiFetch, downloadUrl, errorMessage } from "../../../lib/api";
import { DOCUMENT_STATUS_LABELS, KIND_LABELS, SCAN_STATUS_LABELS, formatBytes, formatDateTime } from "../../../lib/format";
import type { Document } from "../../../lib/types";

const ACCEPTED_TYPES = ".pdf,image/jpeg,image/png,image/webp";
const MAX_BYTES = 25 * 1024 * 1024;

export function DocumentManager() {
  const [documents, setDocuments] = useState<Document[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [kind, setKind] = useState("receipt");
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await apiFetch("/documents");
      if (!response.ok) {
        setLoadError(await errorMessage(response));
        return;
      }
      const body = (await response.json()) as { documents: Document[] };
      setDocuments(body.documents);
      setLoadError("");
    } catch {
      setLoadError("Documents could not be loaded. Check your connection and refresh.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function chooseFile(event: React.ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] ?? null;
    setMessage("");
    if (selected && selected.size > MAX_BYTES) {
      setFile(null);
      setMessage("This file is larger than 25 MB.");
      return;
    }
    setFile(selected);
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setMessage("");
    const body = new FormData();
    body.set("kind", kind);
    body.set("file", file);
    try {
      const response = await apiFetch("/documents", { method: "POST", body });
      if (response.ok) {
        setMessage("Uploaded. The file is scanned before it becomes available.");
        setFile(null);
        setFileKey((key) => key + 1);
        await load();
      } else {
        setMessage(await errorMessage(response));
      }
    } catch {
      setMessage("Upload could not be completed. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(document: Document) {
    if (!window.confirm(`Remove "${document.display_name}"? The stored copy will be deleted.`)) return;
    try {
      const response = await apiFetch(`/documents/${encodeURIComponent(document.id)}`, { method: "DELETE" });
      if (response.ok) {
        setMessage("Document removed.");
        await load();
      } else {
        setMessage(await errorMessage(response));
      }
    } catch {
      setMessage("The document could not be removed. Try again.");
    }
  }

  return (
    <>
      <section className="upload-panel" aria-labelledby="upload-title">
        <h2 id="upload-title">Secure upload</h2>
        <form onSubmit={upload} className="upload-form">
          <label>
            Document type
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              {Object.entries(KIND_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            File
            <input key={fileKey} type="file" accept={ACCEPTED_TYPES} onChange={chooseFile} required />
          </label>
          <button className="primary-button" disabled={busy || !file}>
            {busy ? "Uploading and scanning…" : "Upload document"}
          </button>
        </form>
        {message && (
          <p className="hint" role="status">
            {message}
          </p>
        )}
      </section>

      <section aria-labelledby="list-title" className="document-list-section">
        <h2 id="list-title">Your documents</h2>
        {loadError && (
          <p className="form-error" role="alert">
            {loadError}
          </p>
        )}
        {documents === null && !loadError && <p className="muted">Loading…</p>}
        {documents?.length === 0 && <p className="muted">No documents yet.</p>}
        <ul className="document-list">
          {documents?.map((document) => (
            <li className="document-card" key={document.id}>
              <div className="document-head">
                <div>
                  <h3>{document.display_name}</h3>
                  <p className="muted small">
                    {KIND_LABELS[document.kind] ?? document.kind} · {formatDateTime(document.created_at)}
                  </p>
                </div>
                <span className={`status-pill status-${document.status}`}>
                  {DOCUMENT_STATUS_LABELS[document.status] ?? document.status}
                </span>
              </div>
              <ul className="file-list">
                {document.files.map((item) => (
                  <li key={item.id} className="file-row">
                    <span className="file-name">{item.original_filename}</span>
                    <span className="muted small">
                      {formatBytes(item.size_bytes)} · {SCAN_STATUS_LABELS[item.scan_status] ?? item.scan_status}
                    </span>
                    {item.downloadable && (
                      <a className="text-link" href={downloadUrl(document.id, item.id)} download>
                        Download
                      </a>
                    )}
                  </li>
                ))}
              </ul>
              <div className="document-actions">
                <button className="danger-button compact" type="button" onClick={() => remove(document)}>
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
