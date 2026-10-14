import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, Comparison, money, ms, pct, Run } from "../api";
import { Chip, Donut, EmptyState, ErrorBox, Icon, Info, PageHeader, Skeleton, usePoll } from "../components/kit";

export default function Compare() {
  const [params, setParams] = useSearchParams();
  const { data: runs } = usePoll(api.runs, 10000);
  const a = Number(params.get("a")) || 0;
  const b = Number(params.get("b")) || 0;
  const [cmp, setCmp] = useState<Comparison | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setCmp(null); setErr(null);
    if (a && b) { setBusy(true); api.compare(a, b).then(setCmp).catch((e) => setErr(e.message)).finally(() => setBusy(false)); }
  }, [a, b]);

  const done = (runs ?? []).filter((r) => r.status === "done");
  const label = (r: Run) => `#${r.id} · ${r.name} ${r.version} · ${pct(r.summary.pass_rate, 0)}`;
  const pick = (key: "a" | "b") => (e: React.ChangeEvent<HTMLSelectElement>) => { const p = new URLSearchParams(params); p.set(key, e.target.value); setParams(p); };
  const verdict = cmp?.verdict === "B is better" ? { tone: "ok" as const, icon: "up", title: "B is better", text: "The improvement is bigger than luck alone would explain." }
    : cmp?.verdict === "A is better" ? { tone: "bad" as const, icon: "down", title: "A is better", text: "B got worse, and the drop is bigger than luck alone would explain." }
    : { tone: "mute" as const, icon: "scale", title: "No clear difference", text: "Any gap you see could easily be luck. Add more questions to be sure." };
  const box = { ok: "border-ok/40 bg-green-500/10", bad: "border-bad/40 bg-red-500/10", mute: "border-line bg-panel/80" }[verdict.tone];
  const txt = { ok: "text-ok", bad: "text-bad", mute: "text-slate-300" }[verdict.tone];

  return (
    <div className="space-y-6">
      <PageHeader back={{ to: "/runs", label: "All runs" }} title="Compare two versions" subtitle="Is B really better than A, or is the difference just luck? Each question is compared with itself, so the test is fair." />
      <div className="grid items-end gap-3 sm:grid-cols-[1fr_auto_1fr]">
        <label><span className="label">Version A (before)</span><select className="input" value={a} onChange={pick("a")}><option value={0}>Choose a run…</option>{done.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}</select></label>
        <button className="btn mb-0.5 justify-self-center" title="Swap A and B" onClick={() => setParams({ a: String(b), b: String(a) })} disabled={!a || !b}>⇄</button>
        <label><span className="label">Version B (after)</span><select className="input" value={b} onChange={pick("b")}><option value={0}>Choose a run…</option>{done.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}</select></label>
      </div>
      <ErrorBox error={err} />
      {!a || !b ? <EmptyState icon="scale" title="Pick two runs">Choose a version A and a version B above. Tip: tick two runs on the Runs page and press Compare.</EmptyState> : null}
      {busy && <Skeleton className="h-40" />}
      {cmp && (
        <div className="space-y-6">
          <div className={`card flex flex-wrap items-center gap-6 border p-6 ${box}`}>
            <div className="flex items-center gap-5">
              <Donut value={cmp.pass_rate.a} size={96} stroke={9} label="A" />
              <Icon name="arrow" size={22} className="text-muted" />
              <Donut value={cmp.pass_rate.b} size={96} stroke={9} label="B" />
            </div>
            <div className="min-w-0 flex-1">
              <div className={`flex items-center gap-2 text-xl font-semibold ${txt}`}><Icon name={verdict.icon} size={22} />{verdict.title}</div>
              <p className="mt-1 text-sm text-slate-300">{verdict.text}</p>
              <p className="mt-2 text-xs text-muted">Pass rate {pct(cmp.pass_rate.a)} → {pct(cmp.pass_rate.b)} ({cmp.pass_rate.diff >= 0 ? "+" : ""}{(cmp.pass_rate.diff * 100).toFixed(1)} points). Likely range {(cmp.pass_rate.ci[0] * 100).toFixed(1)} to {(cmp.pass_rate.ci[1] * 100).toFixed(1)}. p = {cmp.pass_rate.p_value.toFixed(3)}<Info text="p is the chance of seeing a gap this big by luck alone if nothing really changed. Below 0.05 is usually called 'real'." /> · {cmp.n_paired} questions in both runs</p>
            </div>
          </div>

          <section className="card overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead><tr><th>Scorer</th><th>A</th><th>B</th><th>Change</th><th>Likely range</th><th>p</th><th>Verdict</th></tr></thead>
              <tbody>
                {Object.entries(cmp.scorers).map(([k, v]) => (
                  <tr key={k} className="border-t border-line/70">
                    <td className="font-mono text-xs text-white">{k}</td><td className="tabular-nums">{v.a.toFixed(3)}</td><td className="tabular-nums">{v.b.toFixed(3)}</td>
                    <td className={`tabular-nums font-medium ${v.diff > 0 ? "text-ok" : v.diff < 0 ? "text-bad" : "text-muted"}`}>{v.diff >= 0 ? "+" : ""}{v.diff.toFixed(3)}</td>
                    <td className="tabular-nums text-muted">{v.ci[0].toFixed(3)} to {v.ci[1].toFixed(3)}</td><td className="tabular-nums">{v.p_value.toFixed(3)}</td>
                    <td><Chip tone={v.verdict === "B is better" ? "ok" : v.verdict === "A is better" ? "bad" : "mute"}>{v.verdict}</Chip></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <div className="grid gap-4 sm:grid-cols-2">
            {(["a", "b"] as const).map((k) => (
              <div key={k} className="card p-4 text-sm"><div className="mb-1 font-semibold text-white">Version {k.toUpperCase()}</div><span className="text-muted">typical {ms(cmp.latency_cost[k].p50_ms)} · slow {ms(cmp.latency_cost[k].p95_ms)} · {money(cmp.latency_cost[k].cost_per_case)} per question</span></div>
            ))}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <ChangeList title="Got worse" tone="bad" ids={cmp.regressed} />
            <ChangeList title="Got better" tone="ok" ids={cmp.improved} />
          </div>
        </div>
      )}
    </div>
  );
}

function ChangeList({ title, tone, ids }: { title: string; tone: "ok" | "bad"; ids: string[] }) {
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center gap-2"><Chip tone={tone}>{ids.length}</Chip><b className="text-white">{title}</b></div>
      {ids.length === 0 ? <span className="text-sm text-muted">none</span> : <div className="flex flex-wrap gap-1.5">{ids.map((i) => <code key={i} className="rounded-md bg-line px-2 py-0.5 text-xs">{i}</code>)}</div>}
    </div>
  );
}
