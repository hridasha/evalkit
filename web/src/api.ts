export type ScorerStat = { n: number; mean: number | null; pass_rate: number | null; skipped: number };
export type Summary = {
  n: number; passed: number; pass_rate: number; pass_ci: [number, number]; errors: number;
  scorers: Record<string, ScorerStat>;
  latency: { mean_ms: number | null; p50_ms: number | null; p95_ms: number | null; max_ms: number | null };
  cost_total: number; cost_per_case: number; tokens_in: number; tokens_out: number;
  by_tag: Record<string, { n: number; pass_rate: number }>;
  warnings?: { scorer: string; count: number; message: string }[];
  classification?: { accuracy: number; macro_f1: number; per_label: Record<string, { precision: number; recall: number; f1: number; support: number }> };
};
export type Run = {
  id: number; name: string; version: string; created_at: number; finished_at: number | null; status: string; summary: Summary;
};
export type Score = { value: number | null; passed: boolean | null; detail: string };
export type CaseResult = {
  case_id: string; repeat: number; input: string; passed: boolean;
  output: { answer: string; contexts: { id: string | null; text: string; score: number | null }[]; latency_ms: number; cost: number; error: string | null };
  scores: Record<string, Score>;
};
export type RunDetail = Run & { results: CaseResult[] };
export type GateResult = { passed: boolean; failures: string[]; checks: { name: string; ok: boolean; detail: string }[] };
export type Comparison = {
  n_paired: number; headline: string; verdict: string; improved: string[]; regressed: string[]; baseline_run_id?: number;
  pass_rate: { a: number; b: number; diff: number; ci: [number, number]; p_value: number; verdict: string };
  scorers: Record<string, { n: number; a: number; b: number; diff: number; ci: [number, number]; p_value: number; verdict: string }>;
  latency_cost: Record<"a" | "b", { p50_ms: number | null; p95_ms: number | null; cost_per_case: number }>;
};
export type Job = {
  id: string; name: string; version: string; status: "running" | "done" | "failed" | "cancelled"; started: number;
  done: number; total: number; passed: number; run_id: number | null; error: string | null;
  result: { summary: Summary; gate: GateResult | null; comparison: Comparison | null } | null;
};
export type TestSetInfo = { name: string; created_at: number; count: number };
export type TestCase = { id: string; input: string; expected: string | null; relevant: string[]; should_abstain: boolean; tags: string[] };
export type Profile = {
  min_pass_rate: number; recommendation: string;
  rows: { run_id: number; label: string; pass_rate: number; cost_per_case: number; p95_ms: number; good_enough: boolean; pareto: boolean; recommended: boolean }[];
};

async function call<T>(method: string, url: string, body?: unknown, form?: FormData): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: form ?? (body ? JSON.stringify(body) : undefined),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const d = (await res.json()).detail;
      msg = typeof d === "string" ? d : JSON.stringify(d);
    } catch { /* keep the status text */ }
    throw new Error(msg);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  runs: () => call<Run[]>("GET", "/api/runs"),
  run: (id: number) => call<RunDetail>("GET", `/api/runs/${id}`),
  deleteRun: (id: number) => call<void>("DELETE", `/api/runs/${id}`),
  compare: (a: number, b: number) => call<Comparison>("GET", `/api/compare?a=${a}&b=${b}`),
  profile: (minPass: number) => call<Profile>("GET", `/api/profile?min_pass_rate=${minPass}`),
  testsets: () => call<TestSetInfo[]>("GET", "/api/testsets"),
  testset: (name: string) => call<TestCase[]>("GET", `/api/testsets/${encodeURIComponent(name)}`),
  deleteTestset: (name: string) => call<void>("DELETE", `/api/testsets/${encodeURIComponent(name)}`),
  uploadTestset: (file: File, name: string) => {
    const f = new FormData();
    f.append("file", file);
    if (name) f.append("name", name);
    return call<{ name: string; count: number }>("POST", "/api/testsets", undefined, f);
  },
  startJob: (b: object) => call<Job>("POST", "/api/jobs", b),
  job: (id: string) => call<Job>("GET", `/api/jobs/${id}`),
  cancelJob: (id: string) => call<void>("DELETE", `/api/jobs/${id}`),
};

export const pct = (v: number | null | undefined, d = 1) => (v == null ? "–" : `${(v * 100).toFixed(d)}%`);
export const ms = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v).toLocaleString()} ms`);
export const money = (v: number) => `$${v.toFixed(v < 0.01 ? 5 : 3)}`;
export const when = (t: number) => new Date(t * 1000).toLocaleString();
