/*
THESIS: A private document workbench that puts the page and its redactions at the center.
OWN-WORLD: Evergreen rail, pale neutral work surface, precise system typography, black ink masks.
STORY: Add a batch, infer identifiers on this Mac, inspect each page, export fresh sanitized PDFs.
FIRST VIEWPORT: Narrow left navigation, local status above a broad import target, policy at right.
FORM: Clinical workstation; registered original/redacted layers make removal directly inspectable.
*/
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  CloudOff,
  Cpu,
  Download,
  Eye,
  FileCheck2,
  FilePlus2,
  FileText,
  FolderOpen,
  Layers,
  LoaderCircle,
  LockKeyhole,
  Monitor,
  Minus,
  Plus,
  ScanLine,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { api, download } from "./api";
import type {
  AppState,
  Box,
  Document,
  Finding,
  ModelState,
  Policy,
} from "./api";

type View = "documents" | "policy" | "engine";
const categories = [
  {
    id: "PATIENT",
    name: "Patient names",
    detail: "Names and identifying initials",
  },
  {
    id: "CLINICIAN",
    name: "Clinician names",
    detail: "Names of care providers",
  },
  {
    id: "ID",
    name: "Medical & personal IDs",
    detail: "MRNs, health cards, and account numbers",
  },
  { id: "DATE", name: "Dates", detail: "Birth dates, visits, and other dates" },
  { id: "AGE", name: "Ages", detail: "Age references in clinical text" },
  {
    id: "PHONE",
    name: "Phone & fax numbers",
    detail: "Contact numbers and extensions",
  },
  {
    id: "EMAIL",
    name: "Email addresses",
    detail: "Personal and professional email",
  },
  {
    id: "LOCATION",
    name: "Addresses & locations",
    detail: "Addresses, postal codes, hospitals, and vendors",
  },
  {
    id: "URL",
    name: "Web addresses",
    detail: "Links that may identify a person",
  },
];
const defaultPolicy: Policy = {
  categories: categories.map((c) => c.id),
  custom_terms: [],
};
const labelNames: Record<string, string> = {
  PATIENT: "Patient name",
  CLINICIAN: "Care provider",
  ID: "Identifier",
  DATE: "Date",
  AGE: "Age",
  PHONE: "Phone number",
  EMAIL: "Email address",
  LOCATION: "Location",
  URL: "Web address",
  CUSTOM: "Custom term",
};
const size = (n: number) =>
  n < 1024
    ? `${n} B`
    : n < 1024 * 1024
      ? `${Math.round(n / 1024)} KB`
      : `${(n / 1024 / 1024).toFixed(1)} MB`;
const statusName = (doc: Document) =>
  doc.status === "review"
    ? doc.reviewed
      ? "Reviewed"
      : "Needs review"
    : doc.status === "awaiting"
      ? "Ready to scan"
      : doc.status === "processing"
        ? "Scanning…"
        : doc.status === "queued"
          ? "Queued for scan"
          : doc.status === "error"
            ? "Needs attention"
            : doc.status === "exported"
              ? "Exported"
              : doc.status;
function Brand() {
  return (
    <div className="brand">
      <span className="brand-symbol">
        <span />
      </span>
      <span>
        veil<span className="brand-dot">.</span>
      </span>
    </div>
  );
}
function App() {
  const [state, setState] = useState<AppState | null>(null),
    [view, setView] = useState<View>("documents"),
    [selected, setSelected] = useState<string | null>(null),
    [detail, setDetail] = useState<Document | null>(null),
    [policy, setPolicy] = useState<Policy>(defaultPolicy),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [dragging, setDragging] = useState(false),
    [help, setHelp] = useState(false),
    [clearConfirm, setClearConfirm] = useState(false),
    [revision, setRevision] = useState(0),
    [reviewSaving, setReviewSaving] = useState(false);
  const reviewLock = useRef(false),
    reviewEpoch = useRef(0);
  const filesRef = useRef<HTMLInputElement>(null),
    folderRef = useRef<HTMLInputElement>(null);
  const noticeTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notify = useCallback((text: string) => {
    setNotice(text);
    if (noticeTimeout.current) clearTimeout(noticeTimeout.current);
    noticeTimeout.current = setTimeout(() => setNotice(""), 5000);
  }, []);
  const refresh = useCallback(async () => {
    try {
      setState(await api<AppState>("/state"));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), 1400);
    return () => clearInterval(id);
  }, [refresh]);
  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    let active = true;
    const load = () => {
      const epoch = reviewEpoch.current;
      return api<Document>(`/documents/${selected}`)
        .then((d) => {
          if (active && epoch === reviewEpoch.current && !reviewLock.current)
            setDetail(d);
        })
        .catch(() => {});
    };
    void load();
    const id = setInterval(() => void load(), 1500);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [selected]);
  const docs = state?.documents || [],
    model = state?.model || { state: "connecting", progress: 0 },
    modelReady = model.state === "ready";
  const documentIds = docs.map((d) => d.id).join(",");
  useEffect(() => {
    if (state)
      setSelected((current) =>
        state.documents.some((d) => d.id === current)
          ? current
          : state.documents[0]?.id || null,
      );
  }, [state?.session_id, documentIds]);
  const reviewed = docs.filter(
    (d) => d.reviewed && ["review", "exported"].includes(d.status),
  );
  const awaiting = docs.filter((d) => d.status === "awaiting");
  const processing = docs.some((d) =>
    ["processing", "queued"].includes(d.status),
  );
  async function run(action: () => Promise<void>, name: string) {
    if (busy) return;
    setBusy(name);
    setError("");
    try {
      await action();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function upload(files: FileList | File[] | null) {
    if (!files?.length) return;
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    await run(async () => {
      const result = await api<{ documents: Document[] }>("/documents", {
        method: "POST",
        body: form,
      });
      setView("documents");
      if (result.documents.length) setSelected(result.documents[0].id);
      if (modelReady)
        await api("/analyze-all", {
          method: "POST",
          body: JSON.stringify({ policy }),
        });
      notify(
        `${result.documents.length} document${result.documents.length === 1 ? "" : "s"} added to this device.`,
      );
    }, "import");
  }
  async function demo() {
    await run(async () => {
      const result = await api<{ documents: Document[] }>("/demo", {
        method: "POST",
      });
      setView("documents");
      if (result.documents.length) setSelected(result.documents[0].id);
      if (modelReady)
        await api("/analyze-all", {
          method: "POST",
          body: JSON.stringify({ policy }),
        });
      notify("Synthetic sample added. No real patient data.");
    }, "demo");
  }
  async function scan() {
    await run(async () => {
      await api("/analyze-all", {
        method: "POST",
        body: JSON.stringify({ policy }),
      });
      notify("Local detection started.");
    }, "scan");
  }
  async function exportAll() {
    if (reviewLock.current) return;
    await run(async () => {
      await download("/export", "veil-redacted.zip", {
        document_ids: reviewed.map((d) => d.id),
      });
      notify("Redacted batch exported.");
    }, "export");
  }
  async function updateReview(
    doc: Document,
    excluded: string[],
    manual: Box[],
    approved = false,
  ) {
    if (reviewLock.current) return;
    reviewLock.current = true;
    reviewEpoch.current++;
    setReviewSaving(true);
    setError("");
    try {
      const updated = await api<Document>(`/documents/${doc.id}/review`, {
        method: "PATCH",
        body: JSON.stringify({
          excluded_finding_ids: excluded,
          manual_boxes: manual,
          reviewed: approved,
        }),
      });
      setDetail(updated);
      setRevision((v) => v + 1);
      await refresh();
      if (approved) notify("Review saved. This document is ready to export.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      reviewEpoch.current++;
      reviewLock.current = false;
      setReviewSaving(false);
    }
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <div className="workspace-label">YOUR WORKSPACE</div>
        <nav aria-label="Main navigation">
          <button
            aria-label="Documents"
            title="Documents"
            className={view === "documents" ? "nav-item active" : "nav-item"}
            onClick={() => setView("documents")}
          >
            <Layers size={18} />
            <span>Documents</span>
            {docs.length > 0 && (
              <span className="nav-count">{docs.length}</span>
            )}
          </button>
          <button
            aria-label="Redaction policy"
            title="Redaction policy"
            className={view === "policy" ? "nav-item active" : "nav-item"}
            onClick={() => setView("policy")}
          >
            <SlidersHorizontal size={18} />
            <span>Redaction policy</span>
          </button>
          <button
            aria-label="Local engine"
            title="Local engine"
            className={view === "engine" ? "nav-item active" : "nav-item"}
            onClick={() => setView("engine")}
          >
            <Cpu size={18} />
            <span>Local engine</span>
            {modelReady && <i className="status-dot" />}
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="privacy-note">
            <div className="privacy-icon">
              <ShieldCheck size={22} />
            </div>
            <h3>Privacy stays here.</h3>
            <p>Your files are processed on your device. Always.</p>
            <button onClick={() => setHelp((v) => !v)}>
              How it works <ArrowUpRight size={13} />
            </button>
          </div>
          <button
            aria-label="This device — local engine"
            className="device-block"
            onClick={() => setView("engine")}
          >
            <span className="device-icon">
              <Monitor size={17} />
            </span>
            <span>
              <b>This device</b>
              <small>Local workspace</small>
            </span>
            <span className="device-dot" />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <ChevronRight size={13} />
            <b>
              {view === "documents"
                ? "Documents"
                : view === "policy"
                  ? "Redaction policy"
                  : "Local engine"}
            </b>
          </div>
          <div className="topbar-right">
            <span className="local-badge">
              <span />
              On-device processing
            </span>
            <button
              className="icon-button help-button"
              aria-label="How Veil works"
              onClick={() => setHelp((v) => !v)}
            >
              <CircleHelp size={18} />
            </button>
          </div>
        </header>
        <main
          className={`main-content ${detail && view === "documents" ? "has-document" : ""}`}
        >
          {error && (
            <div className="error-banner" role="alert">
              <span>{error}</span>
              <button
                className="icon-button"
                aria-label="Dismiss error"
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {help && (
            <section className="help-panel">
              <div>
                <ShieldCheck size={23} />
                <h3>Your files never leave this device</h3>
              </div>
              <p>
                Veil downloads an English clinical detection model once. After
                setup, detection, OCR, review, and export run locally. Review
                every page: AI and OCR can miss sensitive information. Black
                masks are burned into a fresh, image-only PDF; metadata, hidden
                text, and attachments are not copied.
              </p>
              <p>
                Files live in a private temporary session. Clear the batch or
                stop the app to remove the working copies. Your originals and
                downloaded exports stay where you saved them.
              </p>
              <button className="text-button" onClick={() => setHelp(false)}>
                Got it <Check size={14} />
              </button>
            </section>
          )}
          {view === "documents" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="section-kicker">
                    <span /> PRIVATE BY DESIGN
                  </div>
                  <h1>Less personal. Just as useful.</h1>
                  <p>Redact sensitive details. Keep the bigger picture.</p>
                </div>
                <div className="heading-action">
                  {docs.length > 0 ? (
                    <button
                      className="button secondary"
                      onClick={() => filesRef.current?.click()}
                      disabled={!!busy}
                    >
                      <Plus size={16} />
                      Add documents
                    </button>
                  ) : (
                    <span className="quiet-tag">
                      <LockKeyhole size={13} />
                      No cloud. No uploads.
                    </span>
                  )}
                </div>
              </div>
              {docs.length === 0 ? (
                <div className="start-grid">
                  <section className="import-section">
                    <div className="section-title">
                      <h2>Start a new batch</h2>
                      <span>01 / 03</span>
                    </div>
                    <div
                      className={`drop-zone ${dragging ? "dragging" : ""}`}
                      onDragOver={(e) => {
                        e.preventDefault();
                        setDragging(true);
                      }}
                      onDragLeave={() => setDragging(false)}
                      onDrop={(e) => {
                        e.preventDefault();
                        setDragging(false);
                        void upload(e.dataTransfer.files);
                      }}
                    >
                      <div className="paper-scene" aria-hidden="true">
                        <div className="paper-back" />
                        <div className="paper-front">
                          <span className="mini-header" />
                          <span className="paper-line long" />
                          <span className="paper-line" />
                          <div className="paper-row">
                            <span />
                            <b />
                          </div>
                          <span className="paper-line short" />
                          <div className="paper-redaction" />
                          <span className="paper-line long" />
                          <div className="paper-row last">
                            <b />
                            <span />
                          </div>
                          <div className="paper-seal">
                            <Check size={15} strokeWidth={2.5} />
                          </div>
                        </div>
                      </div>
                      <h3>
                        {busy === "import"
                          ? "Preparing your documents…"
                          : "A fresh start for sensitive files."}
                      </h3>
                      <p>Drop your documents here, or choose them below.</p>
                      <button
                        className="button primary import-button"
                        disabled={!!busy}
                        onClick={() => filesRef.current?.click()}
                      >
                        {busy === "import" ? (
                          <LoaderCircle size={16} className="spin" />
                        ) : (
                          <Plus size={17} />
                        )}
                        Choose documents
                      </button>
                      <span className="file-types">
                        PDF, DOCX, TXT, PNG, JPG & TIFF <span>·</span> Up to 100
                        files per batch
                      </span>
                      <button
                        className="text-button folder-button"
                        disabled={!!busy}
                        onClick={() => folderRef.current?.click()}
                      >
                        <FolderOpen size={14} />
                        Or select a folder
                      </button>
                    </div>
                    <div className="sample-row">
                      <span className="sample-icon">
                        <FileText size={19} />
                      </span>
                      <div>
                        <b>Take a look around first</b>
                        <p>
                          Try a synthetic medical record. No real patient data.
                        </p>
                      </div>
                      <button
                        className="text-button"
                        onClick={() => void demo()}
                        disabled={!!busy}
                      >
                        Try a sample <ArrowRight size={15} />
                      </button>
                    </div>
                  </section>
                  <aside className="setup-column">
                    <PolicySummary
                      policy={policy}
                      onEdit={() => setView("policy")}
                    />
                    <div className="engine-summary">
                      <div className="summary-title">
                        <Cpu size={17} />
                        <h3>Your local engine</h3>
                        <span
                          className={`tiny-status ${modelReady ? "ready" : ""}`}
                        >
                          {modelReady ? "Ready" : "Setup"}
                        </span>
                      </div>
                      <p>
                        Clinical AI, built for medical records.
                        <br />
                        Runs entirely on your device.
                      </p>
                      <ModelButton
                        model={model}
                        busy={busy}
                        install={() =>
                          void run(async () => {
                            await api("/model/install", { method: "POST" });
                          }, "install")
                        }
                        manage={() => setView("engine")}
                      />
                    </div>
                    <div className="footnote">
                      <LockKeyhole size={14} />
                      <p>
                        Originals stay untouched.
                        <br />
                        Export a separate, redacted copy.
                      </p>
                    </div>
                  </aside>
                </div>
              ) : (
                <>
                  <div className="batch-toolbar">
                    <div className="batch-info">
                      <h2>
                        Current batch <span>{docs.length}</span>
                      </h2>
                      <span className="batch-divider" />
                      <span>
                        {reviewed.length} reviewed
                        {processing ? " · Scanning locally…" : ""}
                      </span>
                    </div>
                    <div className="batch-actions">
                      {awaiting.length > 0 && (
                        <button
                          className="button secondary small"
                          disabled={!modelReady || !!busy || processing}
                          onClick={() => void scan()}
                        >
                          <ScanLine size={15} />
                          Scan {awaiting.length}{" "}
                          {awaiting.length === 1 ? "file" : "files"}
                        </button>
                      )}
                      <button
                        className="button primary small"
                        disabled={!reviewed.length || !!busy || reviewSaving}
                        onClick={() => void exportAll()}
                      >
                        <ArrowDownToLine size={15} />
                        {busy === "export" ? "Exporting…" : "Export reviewed"}
                        {reviewed.length > 0 && (
                          <span className="button-count">
                            {reviewed.length}
                          </span>
                        )}
                      </button>
                    </div>
                  </div>
                  {!modelReady && (
                    <div className="setup-notice">
                      <Cpu size={18} />
                      <div>
                        <b>
                          {model.state === "downloading"
                            ? "Downloading your local model…"
                            : "Set up the local engine to start detection"}
                        </b>
                        <span>
                          {model.state === "downloading"
                            ? ` ${Math.round(model.progress || 0)}% · This only happens once.`
                            : " Your documents are safely on this device. The model download contains no documents."}
                        </span>
                      </div>
                      <button
                        className="text-button"
                        onClick={() => setView("engine")}
                      >
                        Open engine <ArrowRight size={14} />
                      </button>
                    </div>
                  )}
                  <div className="document-tabs" aria-label="Batch documents">
                    {docs.map((doc, i) => (
                      <button
                        key={doc.id}
                        className={`document-tab ${selected === doc.id ? "selected" : ""}`}
                        onClick={() => setSelected(doc.id)}
                      >
                        <span className="doc-index">
                          {String(i + 1).padStart(2, "0")}
                        </span>
                        <FileText size={17} />
                        <span className="document-tab-text">
                          <b title={doc.name}>{doc.name}</b>
                          <small
                            className={
                              doc.status === "error" ? "error-text" : ""
                            }
                          >
                            {statusName(doc)}
                            {doc.pages > 0
                              ? ` · ${doc.pages} ${doc.pages === 1 ? "page" : "pages"}`
                              : ""}
                          </small>
                        </span>
                        {doc.reviewed ? (
                          <Check size={15} className="green-text" />
                        ) : doc.status === "processing" ? (
                          <LoaderCircle size={15} className="spin" />
                        ) : (
                          <span
                            className={`file-state-dot ${doc.status === "review" ? "amber" : ""}`}
                          />
                        )}
                      </button>
                    ))}
                  </div>
                  {detail && detail.id === selected ? (
                    <DocumentReview
                      key={detail.id}
                      doc={detail}
                      revision={revision}
                      modelReady={modelReady}
                      busy={busy}
                      reviewSaving={reviewSaving}
                      onReview={(excluded, manual, approved) =>
                        void updateReview(detail, excluded, manual, approved)
                      }
                      onAnalyze={() =>
                        void run(async () => {
                          await api(`/documents/${detail.id}/analyze`, {
                            method: "POST",
                            body: JSON.stringify({ policy }),
                          });
                        }, "scan")
                      }
                      onExport={() =>
                        void run(async () => {
                          await download(
                            `/documents/${detail.id}/export`,
                            "document-redacted.pdf",
                          );
                          notify("Redacted PDF exported.");
                        }, "export")
                      }
                      onRemove={() =>
                        void run(async () => {
                          await api(`/documents/${detail.id}`, {
                            method: "DELETE",
                          });
                          setSelected(
                            docs.find((d) => d.id !== detail.id)?.id || null,
                          );
                        }, "remove")
                      }
                    />
                  ) : (
                    <div className="loading-workspace">
                      <LoaderCircle className="spin" size={22} />
                      <p>Opening local document…</p>
                    </div>
                  )}
                  <div className="batch-footer">
                    <span>
                      <LockKeyhole size={13} />
                      Temporary session · files stay on this device
                    </span>
                    {clearConfirm ? (
                      <div className="clear-confirm">
                        <span>Remove all working copies?</span>
                        <button
                          className="text-button danger-text"
                          onClick={() =>
                            void run(async () => {
                              await api("/session", { method: "DELETE" });
                              setSelected(null);
                              setDetail(null);
                              setClearConfirm(false);
                              notify(
                                "Working copies removed from this session.",
                              );
                            }, "clear")
                          }
                        >
                          Clear batch
                        </button>
                        <button
                          className="text-button"
                          onClick={() => setClearConfirm(false)}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        className="text-button"
                        onClick={() => setClearConfirm(true)}
                      >
                        <Trash2 size={13} />
                        Clear batch
                      </button>
                    )}
                  </div>
                </>
              )}
              <div className="workflow-strip">
                <div>
                  <span>1</span>
                  <b>Add documents</b>
                  <small>One file or a whole batch</small>
                </div>
                <ChevronRight size={16} />
                <div>
                  <span>2</span>
                  <b>Detect & review</b>
                  <small>AI finds it. You have the final say.</small>
                </div>
                <ChevronRight size={16} />
                <div>
                  <span>3</span>
                  <b>Export with confidence</b>
                  <small>Permanent black-bar redaction</small>
                </div>
              </div>
            </>
          )}
          {view === "policy" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="section-kicker">
                    <span /> YOUR RULES
                  </div>
                  <h1>Decide what stays private.</h1>
                  <p>A clear policy for every document in your next scan.</p>
                </div>
                <button
                  className="button secondary"
                  onClick={() => setView("documents")}
                >
                  <ArrowLeft size={15} />
                  Back to documents
                </button>
              </div>
              <div className="settings-layout">
                <section className="settings-panel">
                  <div className="settings-panel-heading">
                    <div>
                      <h2>Identifiers to redact</h2>
                      <p>All categories are included by default.</p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => setPolicy(defaultPolicy)}
                    >
                      Reset
                    </button>
                  </div>
                  <div className="policy-options">
                    {categories.map((cat) => (
                      <label className="policy-option" key={cat.id}>
                        <span>
                          <b>{cat.name}</b>
                          <small>{cat.detail}</small>
                        </span>
                        <input
                          type="checkbox"
                          checked={policy.categories.includes(cat.id)}
                          onChange={(e) =>
                            setPolicy((p) => ({
                              ...p,
                              categories: e.target.checked
                                ? [...p.categories, cat.id]
                                : p.categories.filter((c) => c !== cat.id),
                            }))
                          }
                        />
                      </label>
                    ))}
                  </div>
                  <div className="custom-terms">
                    <h3>Something specific?</h3>
                    <p>
                      Add names, codes, or phrases the detector should also
                      remove. One per line.
                    </p>
                    <textarea
                      aria-label="Additional terms to redact"
                      rows={4}
                      placeholder={
                        "Example: a patient nickname\nAn internal account code"
                      }
                      value={policy.custom_terms.join("\n")}
                      onChange={(e) =>
                        setPolicy((p) => ({
                          ...p,
                          custom_terms: e.target.value.split("\n"),
                        }))
                      }
                    />
                    <span>Custom terms stay in this session only.</span>
                  </div>
                </section>
                <aside className="policy-aside">
                  <div className="policy-example">
                    <span className="mini-kicker">THE RESULT</span>
                    <p>
                      Patient <i className="mask medium" />
                      <br />
                      presented on <i className="mask short-mask" />
                      <br />
                      with persistent headache.
                    </p>
                    <span>
                      Keep the clinical context.
                      <br />
                      Remove identifying details.
                    </span>
                  </div>
                  <div className="context-note">
                    <Eye size={19} />
                    <h3>Review is part of the process</h3>
                    <p>
                      Detection can miss identifiers or flag clinical details.
                      Check every page and draw additional masks before
                      exporting.
                    </p>
                    <p>
                      Changes apply to new scans. Re-scan an existing document
                      to apply a new policy; this resets its review.
                    </p>
                  </div>
                </aside>
              </div>
            </>
          )}
          {view === "engine" && (
            <EngineView
              model={model}
              busy={busy}
              onInstall={() =>
                void run(async () => {
                  await api("/model/install", { method: "POST" });
                }, "install")
              }
              onBack={() => setView("documents")}
            />
          )}
          <footer className="app-footer">
            <span>Made for your documents. Built for your privacy.</span>
            <span>
              Veil Local <span className="footer-dot">·</span> v1.0
            </span>
          </footer>
        </main>
      </div>
      <input
        ref={filesRef}
        type="file"
        multiple
        accept=".pdf,.docx,.txt,.png,.jpg,.jpeg,.tif,.tiff"
        className="visually-hidden"
        onChange={(e) => {
          void upload(e.target.files);
          e.target.value = "";
        }}
      />
      <input
        ref={folderRef}
        type="file"
        multiple
        {...{ webkitdirectory: "" }}
        className="visually-hidden"
        onChange={(e) => {
          void upload(
            Array.from(e.target.files || []).filter((f) =>
              /\.(pdf|docx|txt|png|jpe?g|tiff?)$/i.test(f.name),
            ),
          );
          e.target.value = "";
        }}
      />
      {notice && (
        <div className="toast" role="status">
          <Check size={17} />
          {notice}
          <button
            aria-label="Dismiss notification"
            onClick={() => setNotice("")}
          >
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
function PolicySummary({
  policy,
  onEdit,
}: {
  policy: Policy;
  onEdit: () => void;
}) {
  return (
    <section className="policy-summary">
      <div className="summary-title">
        <ShieldCheck size={18} />
        <h3>What gets redacted</h3>
        <button
          className="icon-button"
          aria-label="Edit redaction policy"
          onClick={onEdit}
        >
          <Settings2 size={16} />
        </button>
      </div>
      <p>Your policy. Applied to every file.</p>
      <ul>
        {[
          { name: "Names & personal details", ids: ["PATIENT", "AGE"] },
          { name: "Medical & personal IDs", ids: ["ID"] },
          { name: "Contact information", ids: ["PHONE", "EMAIL", "URL"] },
          { name: "Dates & locations", ids: ["DATE", "LOCATION"] },
          { name: "Clinician names", ids: ["CLINICIAN"] },
        ].map((row) => (
          <li key={row.name}>
            <span
              className={
                row.ids.some((id) => policy.categories.includes(id))
                  ? "check-circle"
                  : "empty-circle"
              }
            >
              <Check size={11} strokeWidth={3} />
            </span>
            {row.name}
          </li>
        ))}
      </ul>
      <button className="text-button" onClick={onEdit}>
        Customize policy <ArrowRight size={14} />
      </button>
      <div className="policy-note">Clinical information stays readable.</div>
    </section>
  );
}
function ModelButton({
  model,
  busy,
  install,
  manage,
}: {
  model: ModelState;
  busy: string;
  install: () => void;
  manage: () => void;
}) {
  const working = ["downloading", "loading"].includes(model.state);
  return model.state === "ready" ? (
    <button className="engine-ready" onClick={manage}>
      <span className="status-dot" />
      Clinical model ready <ArrowRight size={14} />
    </button>
  ) : working ? (
    <div className="download-progress">
      <div>
        <LoaderCircle className="spin" size={14} />
        <span>
          {model.state === "loading" ? "Loading model…" : "Downloading model…"}
        </span>
        <b>{Math.round(model.progress || 0)}%</b>
      </div>
      <progress max={100} value={model.progress || 0} />
    </div>
  ) : (
    <button
      className="button secondary full"
      onClick={install}
      disabled={!!busy || model.state === "connecting"}
    >
      <Download size={15} />
      {model.state === "error" ? "Retry model download" : "Set up local engine"}
      <ArrowRight size={14} />
    </button>
  );
}
function EngineView({
  model,
  busy,
  onInstall,
  onBack,
}: {
  model: ModelState;
  busy: string;
  onInstall: () => void;
  onBack: () => void;
}) {
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="section-kicker">
            <span /> ON YOUR DEVICE
          </div>
          <h1>Small engine. Private by nature.</h1>
          <p>Clinical detection, without a cloud connection.</p>
        </div>
        <button className="button secondary" onClick={onBack}>
          <ArrowLeft size={15} />
          Back to documents
        </button>
      </div>
      <div className="engine-layout">
        <section className="engine-panel">
          <div className="engine-graphic">
            <Cpu size={42} strokeWidth={1.3} />
            <div
              className={`engine-state ${model.state === "ready" ? "ready" : ""}`}
            >
              <span />
              {model.state === "ready"
                ? "Ready for local inference"
                : model.state === "downloading"
                  ? "Downloading model"
                  : model.state === "loading"
                    ? "Loading model"
                    : "One-time setup"}
            </div>
          </div>
          <div className="engine-body">
            <h2>Stanford Clinical De-identifier</h2>
            <p>
              A compact, English-language clinical model trained to locate
              identifying details in medical text. Layered with rules for
              contact information and medical identifiers.
            </p>
            <dl>
              <div>
                <dt>Model</dt>
                <dd>stanford-deidentifier-v2</dd>
              </div>
              <div>
                <dt>Runtime</dt>
                <dd>
                  Local PyTorch ·{" "}
                  {model.device === "mps"
                    ? "Apple GPU (Metal)"
                    : model.device === "cpu"
                      ? "CPU"
                      : "Auto-detected on startup"}
                </dd>
              </div>
              <div>
                <dt>Download</dt>
                <dd>Approximately 440 MB · once</dd>
              </div>
              <div>
                <dt>OCR</dt>
                <dd>Apple Vision on macOS</dd>
              </div>
              <div>
                <dt>Model license</dt>
                <dd>MIT</dd>
              </div>
            </dl>
            {model.error && (
              <div className="engine-error" role="alert">
                {model.error}
              </div>
            )}
            <ModelButton
              model={model}
              busy={busy}
              install={onInstall}
              manage={onBack}
            />
            {model.state !== "ready" && (
              <span className="download-caption">
                Internet is used only to install the model. Your files are never
                sent.
              </span>
            )}
          </div>
        </section>
        <aside className="engine-explainer">
          <h3>A closed loop, on your Mac.</h3>
          <div className="local-flow">
            <div>
              <FilePlus2 size={19} />
              <span>Your documents</span>
            </div>
            <ArrowDown size={17} />
            <div>
              <Cpu size={19} />
              <span>Local model + OCR</span>
            </div>
            <ArrowDown size={17} />
            <div>
              <FileCheck2 size={19} />
              <span>Redacted copies</span>
            </div>
          </div>
          <p>
            <CloudOff size={16} />
            No remote inference endpoints.
            <br />
            No API key. No document uploads.
          </p>
          <div className="context-note">
            <h3>Know its limits</h3>
            <p>
              The model is optimized for English clinical and radiology text.
              Handwriting, unfamiliar layouts, identifiers, or other languages
              may be missed. Always review the output.
            </p>
          </div>
        </aside>
      </div>
    </>
  );
}
function DocumentReview({
  doc,
  revision,
  modelReady,
  busy,
  reviewSaving,
  onReview,
  onAnalyze,
  onExport,
  onRemove,
}: {
  doc: Document;
  revision: number;
  modelReady: boolean;
  busy: string;
  reviewSaving: boolean;
  onReview: (excluded: string[], manual: Box[], approved?: boolean) => void;
  onAnalyze: () => void;
  onExport: () => void;
  onRemove: () => void;
}) {
  const [page, setPage] = useState(0),
    [mode, setMode] = useState<"redacted" | "original">(
      ["review", "exported"].includes(doc.status) ? "redacted" : "original",
    ),
    [drawing, setDrawing] = useState(false),
    [start, setStart] = useState<[number, number] | null>(null),
    [draft, setDraft] = useState<Box | null>(null),
    [activeFinding, setActiveFinding] = useState<string | null>(null),
    [imageError, setImageError] = useState(false),
    [zoom, setZoom] = useState(1),
    [rescanConfirm, setRescanConfirm] = useState(false);
  const canvas = useRef<HTMLDivElement>(null);
  const excluded = doc.excluded_finding_ids || [],
    manual = doc.manual_boxes || [],
    findings = doc.findings || [],
    isScanned = ["review", "exported"].includes(doc.status),
    included = findings.filter((f) => !excluded.includes(f.id)),
    pageFindings = findings.filter((f) =>
      f.boxes?.some((b) => b.page === page),
    );
  useEffect(() => setImageError(false), [page, revision, doc.status]);
  useEffect(() => {
    setMode(isScanned ? "redacted" : "original");
  }, [isScanned]);
  function point(e: React.PointerEvent) {
    const r = canvas.current!.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    ] as [number, number];
  }
  function move(e: React.PointerEvent) {
    if (!start || !drawing || reviewSaving || busy) return;
    const p = point(e);
    setDraft({
      page,
      rect: [
        Math.min(start[0], p[0]),
        Math.min(start[1], p[1]),
        Math.max(start[0], p[0]),
        Math.max(start[1], p[1]),
      ],
    });
  }
  function finish() {
    if (
      !reviewSaving &&
      !busy &&
      draft &&
      draft.rect[2] - draft.rect[0] > 0.004 &&
      draft.rect[3] - draft.rect[1] > 0.004
    )
      onReview(excluded, [...manual, draft]);
    setDraft(null);
    setStart(null);
  }
  const overlayBoxes = activeFinding
    ? findings
        .find((f) => f.id === activeFinding)
        ?.boxes.filter((b) => b.page === page) || []
    : [];
  return (
    <section className="review-workspace">
      <div className="viewer-column">
        <div className="viewer-toolbar">
          <div className="segmented">
            <button
              disabled={!isScanned}
              className={mode === "redacted" ? "selected" : ""}
              onClick={() => setMode("redacted")}
            >
              <ShieldCheck size={14} />
              Redacted
            </button>
            <button
              className={mode === "original" ? "selected" : ""}
              onClick={() => setMode("original")}
            >
              <Eye size={14} />
              Original
            </button>
          </div>
          <div className="viewer-tools">
            <button
              className={`button tool-button ${drawing ? "drawing-active" : ""}`}
              disabled={!isScanned || reviewSaving || !!busy}
              onClick={() => {
                setDrawing(!drawing);
                setMode("redacted");
              }}
              title="Draw an additional redaction box"
            >
              <Plus size={14} />
              <span>Add mask</span>
            </button>
            <span className="viewer-tool-divider" />
            {isScanned && (
              <button
                className="icon-button"
                aria-label="Scan again with current policy"
                title="Scan again with current policy"
                disabled={!!busy || reviewSaving}
                onClick={() => setRescanConfirm((v) => !v)}
              >
                <ScanLine size={15} />
              </button>
            )}
            <button
              className="icon-button"
              aria-label="Remove document"
              onClick={onRemove}
              disabled={!!busy}
            >
              <Trash2 size={15} />
            </button>
          </div>
        </div>
        {rescanConfirm && (
          <div className="rescan-hint">
            <span>
              Re-scanning replaces detections, manual masks, and the review for
              this document.
            </span>
            <button
              className="text-button"
              onClick={() => {
                setRescanConfirm(false);
                setDrawing(false);
                onAnalyze();
              }}
            >
              Re-scan
            </button>
            <button
              className="text-button"
              onClick={() => setRescanConfirm(false)}
            >
              Cancel
            </button>
          </div>
        )}
        {drawing && (
          <div className="drawing-hint">
            <Square size={13} />
            Drag across the page to add a permanent black mask.
            <button onClick={() => setDrawing(false)}>Done</button>
          </div>
        )}
        <div className="viewer-stage">
          {doc.status === "processing" && (
            <div className="scan-overlay">
              <LoaderCircle size={20} className="spin" />
              <b>Finding sensitive details…</b>
              <span>Inference is running on this device.</span>
            </div>
          )}
          {doc.status === "error" ? (
            <div className="viewer-empty">
              <FileText size={30} />
              <h3>This file needs attention</h3>
              <p>{doc.error || "The document could not be processed."}</p>
              <button
                className="button secondary"
                onClick={onAnalyze}
                disabled={!modelReady || !!busy}
              >
                Try again
              </button>
            </div>
          ) : doc.pages > 0 && !imageError ? (
            <div
              ref={canvas}
              className={`document-canvas ${drawing ? "drawing" : ""}`}
              style={{ width: `${zoom * 100}%`, maxWidth: `${600 * zoom}px` }}
              onPointerDown={(e) => {
                if (!drawing || !isScanned || reviewSaving || busy) return;
                e.currentTarget.setPointerCapture(e.pointerId);
                setStart(point(e));
                setDraft(null);
              }}
              onPointerMove={move}
              onPointerUp={finish}
              onPointerCancel={() => {
                setStart(null);
                setDraft(null);
              }}
            >
              <img
                alt={`Page ${page + 1}, ${mode === "original" ? "original document" : "redaction preview"}`}
                draggable={false}
                onError={() => setImageError(true)}
                src={`/api/documents/${doc.id}/pages/${page}.png?redacted=${mode === "redacted" && isScanned}&v=${revision}-${doc.finding_count}-${doc.reviewed}`}
              />
              {overlayBoxes.map((box, i) => (
                <div
                  key={i}
                  className="finding-highlight"
                  style={boxStyle(box)}
                />
              ))}
              {draft && <div className="draft-mask" style={boxStyle(draft)} />}
            </div>
          ) : (
            <div className="viewer-empty">
              <FileText size={30} />
              <h3>
                {imageError ? "Preview unavailable" : "Preparing your document"}
              </h3>
              <p>
                {imageError
                  ? "Re-open the document or try importing it again."
                  : "The preview will appear here."}
              </p>
            </div>
          )}
        </div>
        <div className="viewer-bottom">
          <span>
            <FileText size={13} />
            {size(doc.size)}
            {doc.synthetic && (
              <b className="synthetic-label">Synthetic sample</b>
            )}
          </span>
          <div className="page-pagination">
            <button
              className="icon-button"
              aria-label="Previous page"
              disabled={page <= 0}
              onClick={() => setPage((p) => p - 1)}
            >
              <ChevronLeft size={16} />
            </button>
            <span>
              Page <b>{page + 1}</b> of {doc.pages || 1}
            </span>
            <button
              className="icon-button"
              aria-label="Next page"
              disabled={page >= doc.pages - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              <ChevronRight size={16} />
            </button>
          </div>
          <div className="zoom-controls">
            <button
              className="icon-button"
              aria-label="Zoom out"
              disabled={zoom <= 1}
              onClick={() => setZoom((z) => Math.max(1, z - 0.5))}
            >
              <Minus size={12} />
            </button>
            <button
              className="zoom-reset"
              title="Reset to fit"
              onClick={() => setZoom(1)}
            >
              {zoom === 1 ? "Fit" : `${Math.round(zoom * 100)}%`}
            </button>
            <button
              className="icon-button"
              aria-label="Zoom in"
              disabled={zoom >= 3}
              onClick={() => setZoom((z) => Math.min(3, z + 0.5))}
            >
              <Plus size={12} />
            </button>
          </div>
        </div>
      </div>
      <aside className="findings-panel">
        <div className="findings-heading">
          <div>
            <h3>
              Sensitive details <span>{included.length + manual.length}</span>
            </h3>
            <p>
              {isScanned
                ? "Check each page before exporting."
                : doc.status === "processing"
                  ? "Reading your document locally…"
                  : "Start a scan to find identifiers."}
            </p>
          </div>
          <ScanLine size={18} />
        </div>
        {isScanned ? (
          <>
            <div className="findings-list">
              {pageFindings.length === 0 && (
                <div className="no-findings">
                  <ShieldCheck size={23} />
                  <b>No detections on this page</b>
                  <p>Check for missed details and add masks where needed.</p>
                </div>
              )}
              {pageFindings.map((f) => (
                <FindingRow
                  key={f.id}
                  finding={f}
                  included={!excluded.includes(f.id)}
                  active={activeFinding === f.id}
                  disabled={reviewSaving || !!busy}
                  onFocus={() => setActiveFinding(f.id)}
                  onToggle={() =>
                    onReview(
                      excluded.includes(f.id)
                        ? excluded.filter((id) => id !== f.id)
                        : [...excluded, f.id],
                      manual,
                    )
                  }
                />
              ))}
              {manual.map(
                (box, i) =>
                  box.page === page && (
                    <div className="manual-finding" key={i}>
                      <Square size={15} />
                      <span>
                        Manual redaction <small>Drawn on page {page + 1}</small>
                      </span>
                      <button
                        className="icon-button"
                        aria-label={`Remove manual mask ${i + 1}`}
                        disabled={reviewSaving || !!busy}
                        onClick={() =>
                          onReview(
                            excluded,
                            manual.filter((_, j) => i !== j),
                          )
                        }
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ),
              )}
            </div>
            <div className="review-actions">
              <div
                className={`review-status ${doc.reviewed ? "approved" : ""}`}
              >
                {doc.reviewed ? <CheckCheck size={17} /> : <Eye size={17} />}
                <span>
                  {doc.reviewed ? "Review complete" : "Your review is required"}
                </span>
              </div>
              {!doc.reviewed ? (
                <>
                  <p>
                    Check every page, including handwriting, signatures, photos,
                    and barcodes. Add masks for missed details.
                  </p>
                  <button
                    className="button primary full"
                    disabled={reviewSaving || !!busy}
                    onClick={() => onReview(excluded, manual, true)}
                  >
                    <Check size={16} />
                    I’ve reviewed every page
                  </button>
                </>
              ) : (
                <>
                  <p>
                    Exports contain burned-in masks and no original text layer.
                  </p>
                  <button
                    className="button primary full"
                    onClick={onExport}
                    disabled={!!busy || reviewSaving}
                  >
                    <Download size={16} />
                    {busy === "export" ? "Exporting…" : "Export redacted PDF"}
                  </button>
                  <button
                    className="text-button review-again"
                    disabled={reviewSaving || !!busy}
                    onClick={() => onReview(excluded, manual, false)}
                  >
                    Continue reviewing
                  </button>
                </>
              )}
            </div>
          </>
        ) : (
          <div className="scan-prompt">
            <span className="scan-prompt-icon">
              <ScanLine size={28} />
            </span>
            <h3>
              {doc.status === "processing"
                ? "A closer look, locally."
                : "Ready for a closer look."}
            </h3>
            <p>
              {doc.status === "processing"
                ? "The clinical model is identifying sensitive details. Your records stay on this device."
                : "Scan this document with your local clinical model, then review the proposed redactions."}
            </p>
            <button
              className="button primary full"
              onClick={onAnalyze}
              disabled={
                !modelReady ||
                !!busy ||
                ["processing", "queued"].includes(doc.status)
              }
            >
              {doc.status === "processing" ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <ScanLine size={16} />
              )}
              Scan document
            </button>
            {!modelReady && <small>Set up the local engine first.</small>}
          </div>
        )}
        <div className="findings-footer">
          <LockKeyhole size={12} />
          Only redacted copies are exported.
        </div>
      </aside>
    </section>
  );
}
function boxStyle(b: Box) {
  return {
    left: `${b.rect[0] * 100}%`,
    top: `${b.rect[1] * 100}%`,
    width: `${(b.rect[2] - b.rect[0]) * 100}%`,
    height: `${(b.rect[3] - b.rect[1]) * 100}%`,
  };
}
function FindingRow({
  finding,
  included,
  active,
  disabled,
  onFocus,
  onToggle,
}: {
  finding: Finding;
  included: boolean;
  active: boolean;
  disabled: boolean;
  onFocus: () => void;
  onToggle: () => void;
}) {
  return (
    <div
      className={`finding-row ${active ? "active" : ""} ${!included ? "excluded" : ""}`}
      onMouseEnter={onFocus}
    >
      <button className="finding-value" onClick={onFocus}>
        <span>
          {labelNames[finding.label] || finding.label}
          <small>
            {finding.source === "model"
              ? "AI"
              : finding.source === "custom"
                ? "Custom"
                : finding.source === "model + rules"
                  ? "AI + rule"
                  : "Rule"}
          </small>
        </span>
        <b title={finding.text}>{finding.text}</b>
      </button>
      <label
        className="finding-checkbox"
        title={included ? "Remove this redaction" : "Include this redaction"}
      >
        <input
          type="checkbox"
          disabled={disabled}
          checked={included}
          onChange={onToggle}
          aria-label={`Redact ${finding.text}`}
        />
      </label>
    </div>
  );
}
export default App;
