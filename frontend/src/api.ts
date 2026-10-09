// Thin client for the backend. The UI never calculates totals itself; it only shows backend values.

export interface Summary {
  net_total: number;
  processed_events: number;
  pending_ack: number;
  unresolved: number;
  duplicates: number;
  conflicts: number;
  rejected_submissions: number;
}

export interface ItemResult {
  event_id: string | null;
  status: string;
  message: string;
}

export interface PendingRow {
  event_id: string;
  source_id: string;
  type: string;
  quantity: number | null;
  event_time: string;
  received_at: string;
  status: string;
  voided_by_event_id: string | null;
  channel: string;
}

export interface ExceptionRow {
  kind: string;
  event_id: string | null;
  source_id: string | null;
  type: string | null;
  target_event_id: string | null;
  event_time: string | null;
  received_at: string;
  status: string;
  reason: string | null;
  channel: string;
  challenge_id: string | null;
}

export interface MqttOverview {
  candidate_id: string | null;
  configured_candidate_id: string;
  client_id: string | null;
  connection_state: string;
  last_connected_at: string | null;
  last_heartbeat_at: string | null;
  last_challenge_id: string | null;
  last_challenge_at: string | null;
  last_response_status: string | null;
  last_error: string | null;
  last_error_at: string | null;
  topics: { challenge: string; response: string; status: string };
  challenge_counts: { total: number; completed: number; failed: number };
  recent_challenges: {
    challenge_id: string;
    status: string;
    error_code: string | null;
    received_at: string;
    responded_at: string | null;
    event_count: number;
  }[];
}

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  } catch {
    throw new ApiError("Backend unreachable. Check that the api container is running");
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(`Unexpected response (HTTP ${res.status})`);
  }
  if (!res.ok) throw new ApiError(data?.error ?? `Request failed (HTTP ${res.status})`);
  return data as T;
}

const qs = (source: string) => (source ? `&source_id=${encodeURIComponent(source)}` : "");

export const api = {
  summary: (source: string) => request<Summary>(`/api/state?view=summary${qs(source)}`),
  pending: (source: string) => request<{ items: PendingRow[] }>(`/api/state?view=pending${qs(source)}`),
  exceptions: (source: string) => request<{ items: ExceptionRow[] }>(`/api/state?view=exceptions${qs(source)}`),
  mqtt: () => request<MqttOverview>("/api/mqtt/status"),
  submitEvents: (rawJson: string) =>
    request<{ results: ItemResult[] }>("/api/events", { method: "POST", body: rawJson }),
  acknowledge: (eventIds: string[]) =>
    request<{ results: { event_id: string; status: string }[] }>("/api/ack", {
      method: "POST",
      body: JSON.stringify({ event_ids: eventIds, acknowledged_by: "supervisor-dashboard" }),
    }),
};
