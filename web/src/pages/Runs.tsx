import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ms, money, pct, when } from "../api";
import { LineChart } from "../components/Chart";
import { Bar, Chip, EmptyState, ErrorBox, Icon, PageHeader, rateTone, Skeleton, useToast, usePoll } from "../components/kit";

export default function Runs() {
  const { data: runs, error, loading, reload } = usePoll(api.runs, 3000);
  const nav = useNavigate();
  const toast = useToast();
  const [picked, setPicked] = useState<number[]>([]);
  const [name, setName] = useState("");

  const names = useMemo(() => [...new Set((runs ?? []).map((r) => r.name))], [runs]);
  const shown = useMemo(() => (runs ?? []).filter((r) => !name || r.name === name), [runs, name]);
  // the chart follows one app at a time: the selected one, otherwise the app of the newest run
  const chartApp = name || runs?.[0]?.name || "";
  const trend = useMemo(() => (runs ?? []).filter((r) => r.status === "done" && r.name === chartApp).reverse(), [runs, chartApp]);
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]));

  async function remove(id: number) {
    if (!window.confirm(`Delete run #${id}? This cannot be undone.`)) return;
    try { await api.deleteRun(id); setPicked((p) => p.filter((x) => x !== id)); await reload(); toast(`Run #${id} deleted`); } catch (e) { toast((e as Error).message, "bad"); }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Runs" subtitle="Every evaluation you have run. Tick two runs to see whether one is really better than the other."
        actions={<Link to="/new" className="btn btn-primary"><Icon name="play" size={16} />New run</Link>} />
      <ErrorBox error={error} />
      {loading && <Skeleton className="h-64" />}
      {!loading && runs && runs.length === 0 && <EmptyState icon="runs" title="No runs yet" action={<Link to="/new" className="btn btn-primary">Start your first run</Link>}>Upload a test set, point EvalKit at your app, and every run will show up here.</EmptyState>}

      {runs && runs.length > 0 && (
        <>
          {names.length > 1 && (
            <div className="flex flex-wrap gap-2">
              {["", ...names].map((n) => <button key={n || "all"} onClick={() => setName(n)} className={`rounded-full border px-3 py-1 text-sm ${name === n ? "border-accent bg-accent/15 text-white" : "border-line text-muted hover:text-white"}`}>{n || "All apps"}</button>)}
            </div>
          )}
          {trend.length >= 2 && (
            <LineChart title={`Pass rate over runs · ${chartApp}`} unit="% of questions passed" xLabel="run (oldest to newest)" length={trend.length}
              series={[{ name: "pass rate", color: "#7c8cff", values: trend.map((r) => r.summary.pass_rate * 100) }]} />
          )}
          <div className="card overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead><tr><th className="w-10"></th><th>Run</th><th>Pass rate</th><th>Questions</th><th>p95</th><th>Cost / q</th><th>When</th><th></th></tr></thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id} className={`border-t border-line/70 transition hover:bg-white/[0.03] ${picked.includes(r.id) ? "bg-accent/[0.07]" : ""}`}>
                    <td><input type="checkbox" className="h-4 w-4 accent-[#7c8cff]" aria-label={`Select run ${r.id}`} checked={picked.includes(r.id)} onChange={() => toggle(r.id)} /></td>
                    <td><Link className="font-medium text-white hover:text-accent" to={`/runs/${r.id}`}>{r.name}</Link> <span className="text-muted">{r.version}</span> <span className="text-xs text-muted/70">#{r.id}</span>{r.status !== "done" && <> <Chip tone="warn">{r.status}</Chip></>}</td>
                    <td><div className="flex items-center gap-3"><span className="w-12 font-semibold tabular-nums">{pct(r.summary.pass_rate, 0)}</span><Bar value={r.summary.pass_rate} tone={rateTone(r.summary.pass_rate)} className="w-24" /></div></td>
                    <td className="tabular-nums">{r.summary.n ?? "–"}</td>
                    <td className="tabular-nums text-muted">{ms(r.summary.latency?.p95_ms)}</td>
                    <td className="tabular-nums text-muted">{r.summary.cost_per_case == null ? "–" : money(r.summary.cost_per_case)}</td>
                    <td className="whitespace-nowrap text-muted">{when(r.created_at)}</td>
                    <td><button className="rounded-lg p-1.5 text-muted hover:bg-bad/10 hover:text-bad" title="Delete run" onClick={() => remove(r.id)}><Icon name="trash" size={16} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {picked.length > 0 && (
        <div className="fixed inset-x-0 bottom-5 z-40 mx-auto flex w-fit animate-slide-up items-center gap-3 rounded-2xl border border-accent/40 bg-[#0e1220]/95 px-4 py-2.5 shadow-2xl backdrop-blur">
          <span className="text-sm text-slate-300">{picked.length === 1 ? "Pick one more run to compare" : `Runs #${Math.min(...picked)} and #${Math.max(...picked)} selected`}</span>
          <button className="btn btn-primary" disabled={picked.length !== 2} onClick={() => nav(`/compare?a=${Math.min(...picked)}&b=${Math.max(...picked)}`)}><Icon name="scale" size={16} />Compare</button>
          <button className="btn btn-ghost !px-2" onClick={() => setPicked([])} aria-label="Clear selection"><Icon name="x" size={16} /></button>
        </div>
      )}
    </div>
  );
}
