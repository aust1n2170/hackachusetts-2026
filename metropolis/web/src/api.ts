// Typed client for the FastAPI service. Shapes mirror api/app/schemas.py.

export type Traffic = "light" | "normal" | "rush";

export interface Category { id: string; rule_id: string; label: string; need: string; default_acuity: number }
export interface RulesSummary { version: string; categories: Category[] }

export interface Offload {
  minutes: number;
  source: "live_feed" | "historical_median";
  data_age_min: number | null;
  stale: boolean;
}

export interface Recommendation {
  rank: number;
  hospital_id: string;
  name: string;
  short_name: string;
  lat: number;
  lon: number;
  minutes_total: number;
  minutes_drive: number;
  minutes_offload: number;
  rule_id: string;
  capability: string;
  offload: Offload;
  units_en_route: number;
  on_diversion: boolean;
  diversion_exempt: boolean;
}

export interface Excluded { hospital_id: string; short_name: string; rule_id: string; reason: string }

export interface Baseline {
  hospital_id: string;
  short_name: string;
  minutes_drive: number;
  minutes_offload: number;
  minutes_total: number;
  capable: boolean;
  reason: string;
  same_as_top: boolean;
}

export interface Comparison { nearest_capable: Baseline | null; nearest_ed: Baseline | null }

export interface ScoreResponse {
  audit_id: number;
  rules_version: string;
  category: string;
  rule_id: string;
  need: string;
  routing_source: string;
  recommendations: Recommendation[];
  ranked_lower: Recommendation[];
  excluded: Excluded[];
  comparison: Comparison;
}

export interface Hospital {
  id: string;
  name: string;
  short_name: string;
  lat: number;
  lon: number;
  capabilities: string[];
  feed: "live" | "none";
  ed_occupancy_pct: number | null;
  boarding: number | null;
  on_diversion: boolean;
  status_updated_at: string | null;
  units_en_route: number;
  predicted_offload: Offload;
}

export interface Dispatch {
  id: number;
  unit: string;
  hospital_id: string;
  category: string;
  recommended_rank: number | null;
  dispatched_at: string;
  arrived_at: string | null;
  acuity: number;
  position_lat: number | null;
  position_lon: number | null;
  position_updated_at: string | null;
  last_swapped_at: string | null;
}

export interface DispatchIn {
  unit: string;
  hospital_id: string;
  category: string;
  recommended_rank?: number | null;
  score_audit_id?: number | null;
  acuity?: number | null;
  origin_lat?: number | null;
  origin_lon?: number | null;
}

export interface SwapSide {
  dispatch_id: number;
  unit: string;
  category: string;
  acuity: number;
  from_hospital: string;
  to_hospital: string;
  minutes_before: number;
  minutes_after: number;
  drive_before: number;
  drive_after: number;
  offload: Offload;
  position_source: "live" | "incident_origin";
}

export interface SwapSuggestion {
  id: number;
  status: "pending" | "accepted" | "declined" | "expired";
  rules_version: string;
  rule_id: string;
  a: SwapSide;
  b: SwapSide;
  weighted_gain: number;
  pareto: boolean;
  routing_source: string;
  created_at: string;
  decided_at: string | null;
}

// ---- before vs. after (simulation), mirrors SimulationOut etc. in schemas.py

export interface ScenarioSummary { id: string; title: string; description: string; calls: number }
export interface SimHospital { id: string; short_name: string; lat: number; lon: number }
export interface SimCall { id: number; t_min: number; category: string; acuity: number; lat: number; lon: number; label: string }
export interface SimEvent { t_min: number; hospital_id: string; label: string }
export interface SimMetrics {
  assigned: number;
  unassigned: number;
  mean: number;
  median: number;
  acuity1_mean: number | null;
  total: number;
  not_capable: number;
  peak_en_route: number;
  peak_hospital: string | null;
  swaps: number;
}
export interface SimAssignment {
  call_id: number;
  hospital_id: string;
  first_hospital_id: string;
  t_dispatch: number;
  arrive_at: number;
  drive: number;
  offload_at_arrival: number;
  minutes_to_care: number;
  capable: boolean;
  swapped: boolean;
}
export interface SimStrategy { id: string; label: string; metrics: SimMetrics; assignments: SimAssignment[]; timeline: Record<string, number>[] }
export interface Simulation {
  scenario: string;
  title: string;
  description: string;
  rules_version: string;
  traffic: Traffic;
  secondary_transfer_min: number;
  queue_window_min: number;
  hospitals: SimHospital[];
  calls: SimCall[];
  events: SimEvent[];
  strategies: SimStrategy[];
}
export interface DemoState { enabled: boolean; frozen: boolean; scenario: string | null }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(`${res.status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  rules: () => call<RulesSummary>("/rules"),
  score: (body: { category: string; lat: number; lon: number; traffic: Traffic }) =>
    call<ScoreResponse>("/score", { method: "POST", body: JSON.stringify(body) }),
  hospitals: () => call<Hospital[]>("/hospitals"),
  updateStatus: (id: string, body: Partial<Pick<Hospital, "feed" | "ed_occupancy_pct" | "boarding" | "on_diversion">>) =>
    call<{ updated_at: string }>(`/hospitals/${id}/status`, { method: "PUT", body: JSON.stringify(body) }),
  dispatch: (body: DispatchIn) =>
    call<Dispatch>("/dispatches", { method: "POST", body: JSON.stringify(body) }),
  dispatches: () => call<Dispatch[]>("/dispatches?limit=20"),
  arrive: (id: number) => call<Dispatch>(`/dispatches/${id}/arrive`, { method: "POST" }),
  updatePosition: (id: number, body: { lat: number; lon: number }) =>
    call<Dispatch>(`/dispatches/${id}/position`, { method: "PUT", body: JSON.stringify(body) }),
  evaluateSwaps: (body: { traffic: Traffic }) =>
    call<SwapSuggestion[]>("/ranker/evaluate", { method: "POST", body: JSON.stringify(body) }),
  acceptSwap: (id: number) => call<SwapSuggestion>(`/ranker/swaps/${id}/accept`, { method: "POST" }),
  declineSwap: (id: number) => call<SwapSuggestion>(`/ranker/swaps/${id}/decline`, { method: "POST" }),
  scenarios: () => call<ScenarioSummary[]>("/simulate/scenarios"),
  simulate: (scenario: string) => call<Simulation>("/simulate", { method: "POST", body: JSON.stringify({ scenario }) }),
  demo: () => call<DemoState>("/demo"),
  demoLoad: (scenario: string) => call<DemoState>("/demo/load", { method: "POST", body: JSON.stringify({ scenario }) }),
  demoUnfreeze: () => call<DemoState>("/demo/unfreeze", { method: "POST" }),
};
