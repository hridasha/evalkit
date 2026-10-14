import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ms, money, pct } from "../api";
import { Bar, Chip, EmptyState, ErrorBox, Field, Info, PageHeader, Skeleton, usePoll } from "../components/kit";

export default function Profile() {
  const [min, setMin] = useState("80");
  const m = Math.min(100, Math.max(0, Number(min) || 0)) / 100;
  const { data, error, loading } = usePoll(() => api.profile(m), 5000, [m]);
  const empty = error && /no finished runs/.test(error);
  const maxCost = Math.max(...(data?.rows.map((r) => r.cost_per_case) ?? [0]), 1e-9);

  return (
    <div className="space-y-6">
      <PageHeader title="Cost profile" subtitle="Lines your runs up side by side and finds the cheapest setup that is still good enough." />
      <div className="max-w-xs"><Field label="“Good enough” means a pass rate of at least (%)"><input className="input" value={min} onChange={(e) => setMin(e.target.value)} inputMode="numeric" /></Field></div>
      {loading && <Skeleton className="h-40" />}
      {empty ? <EmptyState icon="coin" title="No finished runs yet" action={<Link className="btn btn-primary" to="/new">Start a run</Link>}>Run your app at least once (ideally with two different setups) to compare their cost and quality.</EmptyState> : <ErrorBox error={error} />}
      {data && (
        <>
          <div className={`card border p-5 text-white ${data.rows.some((r) => r.recommended) ? "border-ok/40 bg-green-500/10" : "border-warn/40 bg-amber-500/10"}`}>{data.recommendation}</div>
          <section className="card overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead><tr><th>Setup</th><th>Pass rate</th><th>Cost / question</th><th>Slow answers (p95)</th><th>Notes<Info text="Pareto = no other setup is better on quality, cost and speed all at once." /></th></tr></thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.run_id} className={`border-t border-line/70 ${r.recommended ? "bg-green-500/[0.07]" : ""}`}>
                    <td className="font-medium text-white"><Link className="hover:text-accent" to={`/runs/${r.run_id}`}>#{r.run_id} {r.label}</Link></td>
                    <td><div className="flex items-center gap-3"><span className="w-10 tabular-nums">{pct(r.pass_rate, 0)}</span><Bar value={r.pass_rate} className="w-24" /></div></td>
                    <td><div className="flex items-center gap-3"><span className="w-20 tabular-nums">{money(r.cost_per_case)}</span><Bar value={r.cost_per_case / maxCost} tone="accent" className="w-20" /></div></td>
                    <td className="tabular-nums text-muted">{ms(r.p95_ms)}</td>
                    <td className="space-x-1">{r.recommended && <Chip tone="ok">recommended</Chip>}{r.good_enough ? <Chip tone="accent">good enough</Chip> : <Chip>below target</Chip>}{r.pareto && <Chip tone="warn">pareto</Chip>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}
