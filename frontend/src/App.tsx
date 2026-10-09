import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, ExceptionRow, ItemResult, MqttOverview, PendingRow, Summary } from "./api";

const REFRESH_MS = 5000;

// Demo payloads. "{n}" is replaced with the current ID set so the demo can be replayed on a fresh set.
const SAMPLES: { label: string; body: (n: number) => unknown }[] = [
  {
    label: "COUNT +5",
    body: (n) => ({ source_id: "LINE-01", event_id: `EV-${n}01`, type: "COUNT", quantity: 5, target_event_id: null, event_time: nowIso() }),
  },
  {
    label: "VOID before COUNT",
    body: (n) => ({ source_id: "LINE-01", event_id: `EV-${n}11`, type: "VOID", target_event_id: `EV-${n}10`, event_time: nowIso() }),
  },
  {
    label: "Target COUNT +3",
    body: (n) => ({ source_id: "LINE-01", event_id: `EV-${n}10`, type: "COUNT", quantity: 3, event_time: nowIso() }),
  },
  {
    label: "Mixed batch",
    body: (n) => [
      { source_id: "LINE-02", event_id: `EV-${n}20`, type: "COUNT", quantity: 8, event_time: nowIso() },
      { source_id: "LINE-02", event_id: `EV-${n}21`, type: "COUNT", quantity: -1, event_time: nowIso() },
      { source_id: "LINE-02", event_id: `EV-${n}22`, type: "VOID", target_event_id: `EV-${n}20`, event_time: nowIso() },
    ],
  },
  {
    label: "Conflict",
    body: (n) => ({ source_id: "LINE-01", event_id: `EV-${n}01`, type: "COUNT", quantity: 9, event_time: nowIso() }),
  },
];

function nowIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function fmtTime(value: string | null | undefined) {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

function Badge({ status }: { status: string }) {
  const key = status.split(" ")[0].toLowerCase().replace(/_/g, "-");
  return <span className={`badge badge-${key}`}>{status.replace(/_/g, " ")}</span>;
}

type Tab = "pending" | "exceptions";

export default function App() {
  const [source, setSource] = useState("");
  const [sourceDraft, setSourceDraft] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [pending, setPending] = useState<PendingRow[] | null>(null);
  const [exceptions, setExceptions] = useState<ExceptionRow[] | null>(null);
  const [mqtt, setMqtt] = useState<MqttOverview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [tab, setTab] = useState<Tab>("pending");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [ackBusy, setAckBusy] = useState(false);
  const [ackMessage, setAckMessage] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const [idSet, setIdSet] = useState(1);
  const [draft, setDraft] = useState(() => JSON.stringify(SAMPLES[0].body(1), null, 2));
  const [submitBusy, setSubmitBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [results, setResults] = useState<ItemResult[] | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [s, p, e, m] = await Promise.all([api.summary(source), api.pending(source), api.exceptions(source), api.mqtt()]);
      setSummary(s);
      setPending(p.items);
      setExceptions(e.items);
      setMqtt(m);
      setLoadError(null);
      setLastUpdated(new Date());
      setSelected((prev) => new Set([...prev].filter((id) => p.items.some((r) => r.event_id === id))));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Failed to load data");
    } finally {
      setRefreshing(false);
    }
  }, [source]);

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, REFRESH_MS);
    return () => window.clearInterval(t);
  }, [refresh]);

  const jsonError = useMemo(() => {
    if (!draft.trim()) return "Enter a JSON event or an array of events";
    try {
      const parsed = JSON.parse(draft);
      if (typeof parsed !== "object" || parsed === null) return "Top level must be an object or an array";
      return null;
    } catch (e) {
      return `Invalid JSON: ${(e as Error).message}`;
    }
  }, [draft]);

  async function submit() {
    if (jsonError) return;
    setSubmitBusy(true);
    setSubmitError(null);
    try {
      const res = await api.submitEvents(draft);
      setResults(res.results);
      await refresh();
    } catch (err) {
      setResults(null);
      setSubmitError(err instanceof ApiError ? err.message : "Submission failed");
    } finally {
      setSubmitBusy(false);
    }
  }

  async function acknowledge() {
    if (selected.size === 0) return;
    setAckBusy(true);
    setAckMessage(null);
    try {
      const res = await api.acknowledge([...selected]);
      const acked = res.results.filter((r) => r.status === "ACKED").length;
      const other = res.results.filter((r) => r.status !== "ACKED");
      setAckMessage({
        kind: "ok",
        text: `${acked} acknowledged` + (other.length ? ` · ${other.map((r) => `${r.event_id}: ${r.status}`).join(", ")}` : ""),
      });
      setSelected(new Set());
      await refresh();
    } catch (err) {
      setAckMessage({ kind: "err", text: err instanceof ApiError ? err.message : "Acknowledgement failed" });
    } finally {
      setAckBusy(false);
    }
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  const allSelected = !!pending && pending.length > 0 && pending.every((r) => selected.has(r.event_id));

  const kpis: { key: keyof Summary; label: string; hint: string; tone?: string }[] = [
    { key: "net_total", label: "Net total", hint: "Accepted COUNTs minus applied VOIDs", tone: "primary" },
    { key: "processed_events", label: "Processed events", hint: "Completed COUNT + VOID events" },
    { key: "pending_ack", label: "Pending acknowledgement", hint: "Processed, not yet reviewed", tone: "warn" },
    { key: "unresolved", label: "Unresolved references", hint: "VOIDs waiting for their COUNT", tone: "warn" },
    { key: "duplicates", label: "Duplicates", hint: "Identical resends, not counted" },
    { key: "conflicts", label: "Conflicts", hint: "Same ID, different data", tone: "danger" },
  ];

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">NB</span>
            <p className="eyebrow">NorthBridge Garments</p>
          </div>
          <h1>Production Control</h1>
          <p className="lede">
            One source of truth for the supervisor: true piece totals, events waiting for review, exceptions that need attention
            and whether line counters are responding.
          </p>
        </div>
        <form
          className="filter"
          onSubmit={(e) => {
            e.preventDefault();
            setSource(sourceDraft.trim());
          }}
        >
          <label htmlFor="src">Production line</label>
          <div className="filter-row">
            <input id="src" placeholder="All lines (e.g. LINE-01)" value={sourceDraft} onChange={(e) => setSourceDraft(e.target.value)} />
            <button type="submit" className="btn">Apply</button>
            <button type="button" className="btn ghost" onClick={refresh} disabled={refreshing}>
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
          </div>
          <span className="muted small">
            {source ? `Filtered: ${source}` : "All lines"} · {lastUpdated ? `updated ${lastUpdated.toLocaleTimeString()}` : "loading…"}
          </span>
        </form>
      </header>

      {loadError && <div className="alert err" role="alert">{loadError}. Showing last known values.</div>}

      <section className="kpis" aria-label="Production indicators">
        {kpis.map((k) => (
          <div key={k.key} className={`kpi ${k.tone ?? ""}`}>
            <span className="kpi-label">{k.label}</span>
            <span className="kpi-value">{summary ? summary[k.key] : <span className="skeleton" />}</span>
            <span className="kpi-hint">{k.hint}</span>
          </div>
        ))}
      </section>

      <div className="grid">
        <section className="card">
          <div className="card-head">
            <h2>Submit count or correction</h2>
            <span className="muted small">Operator · manual entry</span>
          </div>
          <div className="samples">
            {SAMPLES.map((s) => (
              <button key={s.label} className="chip" onClick={() => setDraft(JSON.stringify(s.body(idSet), null, 2))}>
                {s.label}
              </button>
            ))}
            <button
              className="chip ghost"
              title="Use a fresh set of event IDs for the samples"
              onClick={() => {
                const n = idSet + 1;
                setIdSet(n);
                setDraft(JSON.stringify(SAMPLES[0].body(n), null, 2));
              }}
            >
              New ID set ({idSet})
            </button>
          </div>
          <textarea
            className={`editor ${jsonError ? "invalid" : ""}`}
            spellCheck={false}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="Event JSON"
          />
          <div className="row">
            <span className={`small ${jsonError ? "text-err" : "muted"}`}>{jsonError ?? "Valid JSON · one event or an array"}</span>
            <button className="btn" onClick={submit} disabled={!!jsonError || submitBusy}>
              {submitBusy ? "Submitting…" : "Submit"}
            </button>
          </div>
          {submitError && <div className="alert err">{submitError}</div>}
          {results && (
            <ul className="results">
              {results.length === 0 && <li className="muted">Empty array. Nothing to process.</li>}
              {results.map((r, i) => (
                <li key={i}>
                  <span className="mono">#{i + 1} {r.event_id ?? "(no id)"}</span>
                  <Badge status={r.status} />
                  <span className="small muted">{r.message}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <MqttPanel mqtt={mqtt} />
      </div>

      <section className="card">
        <div className="card-head">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "pending"} className={tab === "pending" ? "active" : ""} onClick={() => setTab("pending")}>
              Pending review{pending && <span className="count">{pending.length}</span>}
            </button>
            <button role="tab" aria-selected={tab === "exceptions"} className={tab === "exceptions" ? "active" : ""} onClick={() => setTab("exceptions")}>
              Exceptions{exceptions && <span className="count">{exceptions.length}</span>}
            </button>
          </div>
          {tab === "pending" && (
            <button className="btn" onClick={acknowledge} disabled={selected.size === 0 || ackBusy}>
              {ackBusy ? "Acknowledging…" : `Acknowledge selected (${selected.size})`}
            </button>
          )}
        </div>
        {ackMessage && tab === "pending" && <div className={`alert ${ackMessage.kind}`}>{ackMessage.text}</div>}

        {tab === "pending" ? (
          <div className="table-wrap">
            {pending === null ? (
              <p className="muted pad">Loading…</p>
            ) : pending.length === 0 ? (
              <div className="empty">
                <strong>Nothing waiting for review</strong>
                Every processed COUNT is acknowledged. New counts from the lines will appear here.
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>
                      <input
                        type="checkbox"
                        aria-label="Select all"
                        checked={allSelected}
                        onChange={() => setSelected(allSelected ? new Set() : new Set(pending.map((r) => r.event_id)))}
                      />
                    </th>
                    <th>Event</th>
                    <th>Line</th>
                    <th>Qty</th>
                    <th>Event time</th>
                    <th>Received</th>
                    <th>Channel</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {pending.map((r) => (
                    <tr key={r.event_id} className={selected.has(r.event_id) ? "sel" : ""} onClick={() => toggle(r.event_id)}>
                      <td>
                        <input type="checkbox" checked={selected.has(r.event_id)} onChange={() => toggle(r.event_id)} onClick={(e) => e.stopPropagation()} aria-label={`Select ${r.event_id}`} />
                      </td>
                      <td className="mono" data-label="Event">{r.event_id}</td>
                      <td data-label="Line">{r.source_id}</td>
                      <td data-label="Qty">{r.quantity}</td>
                      <td data-label="Event time">{fmtTime(r.event_time)}</td>
                      <td data-label="Received">{fmtTime(r.received_at)}</td>
                      <td data-label="Channel">{r.channel}</td>
                      <td data-label="Status">
                        {r.voided_by_event_id ? <span className="badge badge-voided">Reversed by {r.voided_by_event_id}</span> : <Badge status={r.status} />}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ) : (
          <div className="table-wrap">
            {exceptions === null ? (
              <p className="muted pad">Loading…</p>
            ) : exceptions.length === 0 ? (
              <div className="empty">
                <strong>No exceptions</strong>
                No unresolved references, rejected submissions or conflicts right now.
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Kind</th>
                    <th>Event</th>
                    <th>Line</th>
                    <th>Type</th>
                    <th>Target</th>
                    <th>Reason</th>
                    <th>Received</th>
                    <th>Channel</th>
                  </tr>
                </thead>
                <tbody>
                  {exceptions.map((r, i) => (
                    <tr key={i}>
                      <td data-label="Kind"><Badge status={r.kind} /></td>
                      <td className="mono" data-label="Event">{r.event_id ?? "-"}</td>
                      <td data-label="Line">{r.source_id ?? "-"}</td>
                      <td data-label="Type">{r.type ?? "-"}</td>
                      <td className="mono" data-label="Target">{r.target_event_id ?? "-"}</td>
                      <td className="reason" data-label="Reason">{r.reason ?? "-"}</td>
                      <td data-label="Received">{fmtTime(r.received_at)}</td>
                      <td data-label="Channel">{r.channel}{r.challenge_id ? ` · ${r.challenge_id}` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </section>

      <footer className="muted small foot">Values come from PostgreSQL through the REST API · auto-refresh every {REFRESH_MS / 1000}s</footer>
    </div>
  );
}

function MqttPanel({ mqtt }: { mqtt: MqttOverview | null }) {
  const state = mqtt?.connection_state ?? "UNKNOWN";
  const tone = state === "ONLINE" ? "ok" : state === "UNKNOWN" || state === "CONNECTING" ? "idle" : "bad";
  return (
    <section className="card">
      <div className="card-head">
        <h2>Device link (MQTT)</h2>
        <span className={`link-state link-${tone}`}>{state}</span>
      </div>
      {!mqtt ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="counters">
            <div className="counter"><b>{mqtt.challenge_counts.total}</b><span>Challenges</span></div>
            <div className="counter"><b>{mqtt.challenge_counts.completed}</b><span>Completed</span></div>
            <div className="counter"><b>{mqtt.challenge_counts.failed}</b><span>Failed</span></div>
          </div>
          <dl className="facts">
            <dt>Candidate ID</dt>
            <dd className="mono">{mqtt.candidate_id ?? mqtt.configured_candidate_id}</dd>
            <dt>Client ID</dt>
            <dd className="mono">{mqtt.client_id ?? "-"}</dd>
            <dt>Last heartbeat</dt>
            <dd>{fmtTime(mqtt.last_heartbeat_at)}</dd>
            <dt>Last challenge</dt>
            <dd className="mono">{mqtt.last_challenge_id ?? "-"}</dd>
            <dt>Challenge time</dt>
            <dd>{fmtTime(mqtt.last_challenge_at)}</dd>
            <dt>Last response</dt>
            <dd>{mqtt.last_response_status ? <Badge status={mqtt.last_response_status} /> : "-"}</dd>
            <dt>Last error</dt>
            <dd className={mqtt.last_error ? "text-err" : ""}>
              {mqtt.last_error ? `${mqtt.last_error} (${fmtTime(mqtt.last_error_at)})` : "None"}
            </dd>
            <dt>Topics</dt>
            <dd className="mono small">{mqtt.topics.challenge}<br />{mqtt.topics.response}<br />{mqtt.topics.status}</dd>
          </dl>
          {mqtt.recent_challenges.length > 0 && (
            <ul className="results compact">
              {mqtt.recent_challenges.slice(0, 5).map((c) => (
                <li key={c.challenge_id}>
                  <span className="mono">{c.challenge_id}</span>
                  <Badge status={c.error_code ? `${c.status} ${c.error_code}` : c.status} />
                  <span className="small muted">{c.event_count} events · {fmtTime(c.received_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
