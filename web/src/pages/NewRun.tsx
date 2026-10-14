import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, Job, money, ms, pct } from "../api";
import { Chip, Donut, ErrorBox, Field, Icon, PageHeader, usePoll } from "../components/kit";

const TEMPLATE = `name = "my-app"

[target]
type = "http"
url = "http://localhost:8100/ask"      # your app's endpoint
body = { question = "{{input}}" }      # {{input}} is replaced by each question
answer_path = "answer"                 # where the answer is in the JSON reply
contexts_path = "contexts"             # optional: retrieved passages (for retrieval scorers)
tokens_in_path = "usage.input_tokens"  # optional: lets EvalKit count cost
tokens_out_path = "usage.output_tokens"
input_price_per_m = 0.30               # USD per million tokens (example prices)
output_price_per_m = 2.50

[run]
concurrency = 4

[[scorers]]
name = "contains"
[[scorers]]
name = "hit_rate"
k = 4
[[scorers]]
name = "abstain"

[gate]
min_pass_rate = 0.7
max_errors = 0
max_regression = 0.05
`;

type ScorerInfo = { name: string; description: string };

function useJob(id: string | null) {
  const [job, setJob] = useState<Job | null>(null);
  useEffect(() => {
    if (!id) { setJob(null); return; }
    let closed = false;
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/jobs/${id}`);
    ws.onmessage = (m) => setJob(JSON.parse(m.data));
    ws.onclose = () => { if (!closed) api.job(id).then(setJob).catch(() => {}); };
    return () => { closed = true; ws.close(); };
  }, [id]);
  return job;
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="card p-5">
      <div className="mb-4 flex items-center gap-3"><span className="grid h-7 w-7 place-items-center rounded-full bg-accent/20 text-xs font-bold text-accent">{n}</span><h2 className="h2">{title}</h2></div>
      {children}
    </section>
  );
}

export default function NewRun() {
  const [params] = useSearchParams();
  const { data: sets } = usePoll(api.testsets, 8000);
  const [scorers, setScorers] = useState<ScorerInfo[]>([]);
  const [testset, setTestset] = useState(params.get("testset") ?? "");
  const [config, setConfig] = useState(() => { try { return localStorage.getItem("evalkit.config") || TEMPLATE; } catch { return TEMPLATE; } });
  const [version, setVersion] = useState("");
  const [repeats, setRepeats] = useState("1");
  const [paraphrases, setParaphrases] = useState(false);
  const [limit, setLimit] = useState("0");
  const [add, setAdd] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);

  useEffect(() => { if (!testset && sets && sets[0]) setTestset(sets[0].name); }, [sets, testset]);
  useEffect(() => { fetch("/api/scorers").then((r) => r.json()).then((l: ScorerInfo[]) => setScorers(l)).catch(() => {}); }, []);
  useEffect(() => { try { localStorage.setItem("evalkit.config", config); } catch { /* private window */ } }, [config]);
  useEffect(() => {
    let saved = false;
    try { saved = !!localStorage.getItem("evalkit.saved"); } catch { /* ignore */ }
    if (saved) return;
    fetch("/api/health").then((r) => r.json()).then((h) => { if (h.demo_url) setConfig(TEMPLATE.replace("http://localhost:8100/ask", h.demo_url)); }).catch(() => {});
  }, []);

  const edit = (v: string) => { setConfig(v); try { localStorage.setItem("evalkit.saved", "1"); } catch { /* ignore */ } };
  const addScorer = () => { if (!add) return; edit(`${config.replace(/\s*$/, "")}\n\n[[scorers]]\nname = "${add}"\n`); setAdd(""); };
  const reset = () => { setConfig(TEMPLATE); try { localStorage.removeItem("evalkit.saved"); } catch { /* ignore */ } };

  async function start() {
    setErr(null);
    try {
      const j = await api.startJob({ config, testset, version: version || null, repeats: Number(repeats) || 1, paraphrases, limit: Number(limit) || 0 });
      setJobId(j.id);
    } catch (e) { setErr((e as Error).message); }
  }

  const running = job?.status === "running";
  const res = job?.result;
  const set = sets?.find((s) => s.name === testset);

  return (
    <div className="space-y-6">
      <PageHeader title="New run" subtitle="Three steps: choose your questions, describe your app, start. The same config works on the command line as evalkit.toml." />
      <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
        <div className="space-y-5">
          <Step n={1} title="Choose your questions">
            {sets && sets.length === 0 ? (
              <p className="text-sm text-muted">You have no test sets yet. <Link to="/testsets" className="text-accent hover:underline">Upload one first</Link>.</p>
            ) : (
              <Field label="Test set" hint={set ? `${set.count} questions` : undefined}>
                <select className="input" value={testset} onChange={(e) => setTestset(e.target.value)}>{sets?.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}</select>
              </Field>
            )}
          </Step>

          <Step n={2} title="Describe your app and how to score it">
            <textarea className="input h-80 font-mono text-xs leading-relaxed" spellCheck={false} value={config} onChange={(e) => edit(e.target.value)} aria-label="Config" />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <select className="input w-56" value={add} onChange={(e) => setAdd(e.target.value)} aria-label="Add a scorer">
                <option value="">Add a scorer…</option>{scorers.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
              <button className="btn" onClick={addScorer} disabled={!add}>Add</button>
              <button className="btn btn-ghost ml-auto text-xs" onClick={reset}>Reset to example</button>
            </div>
            {add && <p className="mt-2 text-xs text-muted">{scorers.find((s) => s.name === add)?.description}</p>}
          </Step>
        </div>

        <div className="space-y-5 xl:sticky xl:top-8 xl:self-start">
          <Step n={3} title="Start">
            <div className="space-y-3">
              <Field label="Version label" hint="A name for what you changed, e.g. prompt-v2."><input className="input" value={version} onChange={(e) => setVersion(e.target.value)} placeholder="baseline" /></Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Repeats" hint="ask each N times"><input className="input" value={repeats} onChange={(e) => setRepeats(e.target.value)} /></Field>
                <Field label="Only first N" hint="0 = all"><input className="input" value={limit} onChange={(e) => setLimit(e.target.value)} /></Field>
              </div>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-[#7c8cff]" checked={paraphrases} onChange={(e) => setParaphrases(e.target.checked)} /> Also ask the paraphrases</label>
              <ErrorBox error={err} />
              <button className="btn btn-primary w-full" onClick={start} disabled={!testset || running}><Icon name="play" size={16} />{running ? "Running…" : "Start run"}</button>
            </div>
          </Step>
        </div>
      </div>

      {job && (
        <section className="card animate-slide-up space-y-4 p-6">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="h2">{job.name} {job.version}</h2>
            <Chip tone={job.status === "done" ? "ok" : running ? "accent" : "bad"}>{job.status}</Chip>
            <span className="text-sm text-muted">{job.done} / {job.total} questions · {job.passed} passing</span>
            {running && <button className="btn btn-danger ml-auto" onClick={() => api.cancelJob(job.id)}>Cancel</button>}
          </div>
          <div className="h-2.5 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full bg-gradient-to-r from-accent to-[#a5b0ff] transition-all duration-300" style={{ width: `${Math.min(100, (job.done / Math.max(1, job.total)) * 100)}%` }} /></div>
          <ErrorBox error={job.error} />
          {res && (
            <div className="grid gap-6 md:grid-cols-[150px_1fr]">
              <Donut value={res.summary.pass_rate} size={140} stroke={12} label="pass rate" />
              <div className="space-y-3">
                <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted">
                  <span>Errors <b className="text-white">{res.summary.errors}</b></span><span>p95 <b className="text-white">{ms(res.summary.latency.p95_ms)}</b></span>
                  <span>Cost <b className="text-white">{money(res.summary.cost_per_case)}</b> / question</span><span>95% range <b className="text-white">{pct(res.summary.pass_ci[0], 0)}–{pct(res.summary.pass_ci[1], 0)}</b></span>
                </div>
                {res.comparison && <p className="text-sm text-slate-300">Versus run #{res.comparison.baseline_run_id}: {res.comparison.headline}</p>}
                {res.gate && (
                  <div className={`rounded-xl border p-3.5 text-sm ${res.gate.passed ? "border-ok/40 bg-green-500/10" : "border-bad/40 bg-red-500/10"}`}>
                    <b className={res.gate.passed ? "text-ok" : "text-bad"}>Quality gate {res.gate.passed ? "passed" : "FAILED"}</b>
                    <ul className="mt-1.5 space-y-0.5">{res.gate.checks.map((c) => <li key={c.name} className={c.ok ? "text-muted" : "text-bad"}>{c.ok ? "✓" : "✗"} {c.name}: {c.detail}</li>)}</ul>
                  </div>
                )}
                {job.run_id && <Link className="btn btn-primary" to={`/runs/${job.run_id}`}>Open run #{job.run_id} <Icon name="arrow" size={16} /></Link>}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
