import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, CaseResult, ms, money, pct, RunDetail as RunData, when } from "../api";
import { Bar, Chip, Code, Donut, ErrorBox, Icon, Info, PageHeader, Skeleton, StatCard, Warning } from "../components/kit";

export default function RunDetail() {
  const { id = "" } = useParams();
  const [run, setRun] = useState<RunData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [only, setOnly] = useState<"failing" | "all">("failing");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [help, setHelp] = useState<Record<string, string>>({});

  useEffect(() => { setRun(null); api.run(Number(id)).then(setRun).catch((e) => setErr(e.message)); }, [id]);
  useEffect(() => { fetch("/api/scorers").then((r) => r.json()).then((l: { name: string; description: string }[]) => setHelp(Object.fromEntries(l.map((s) => [s.name, s.description])))).catch(() => {}); }, []);

  const shown = useMemo(() => (run?.results ?? []).filter((r) => (only === "all" || !r.passed) && `${r.case_id} ${r.input} ${r.output.answer}`.toLowerCase().includes(q.toLowerCase())), [run, only, q]);

  if (err) return <ErrorBox error={err} />;
  if (!run) return <div className="space-y-4"><Skeleton className="h-32" /><Skeleton className="h-64" /></div>;
  const s = run.summary;
  const failing = run.results.filter((r) => !r.passed).length;
  const tags = Object.entries(s.by_tag);
  return (
    <div className="space-y-6">
      <PageHeader back={{ to: "/runs", label: "All runs" }} title={<>{run.name} <span className="font-normal text-muted">{run.version}</span></>}
        subtitle={`Run #${run.id} · ${when(run.created_at)}`}
        actions={<><a className="btn" href={`/api/runs/${run.id}/report.html`} target="_blank" rel="noreferrer"><Icon name="external" size={15} />Open report</a><Link className="btn" to="/runs"><Icon name="scale" size={15} />Compare…</Link></>} />

      {(s.warnings?.length ?? 0) > 0 && (
        <Warning>
          <b>{s.warnings!.reduce((a, w) => a + w.count, 0)} scores could not be calculated.</b>
          {s.warnings!.slice(0, 2).map((w) => <div key={w.message} className="mt-1 text-xs opacity-90"><code>{w.scorer}</code> ({w.count}×): {w.message}</div>)}
        </Warning>
      )}

      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <div className="card flex flex-col items-center justify-center p-6">
          <Donut value={s.pass_rate} size={150} stroke={13} label="pass rate" />
          <p className="mt-3 text-center text-xs text-muted">{s.passed} of {s.n} questions passed<br />95% range {pct(s.pass_ci[0], 0)}–{pct(s.pass_ci[1], 0)}<Info text="With few questions the true pass rate could be a bit higher or lower. More questions make this range narrower." /></p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard label="Failing" icon="x" value={failing} tone={failing ? "bad" : "ok"} sub="questions to look at" />
          <StatCard label="Errors" icon="alert" value={s.errors} tone={s.errors ? "bad" : "ok"} sub="app did not answer" />
          <StatCard label="Speed" icon="runs" value={ms(s.latency.p50_ms)} sub={`p95 ${ms(s.latency.p95_ms)}`} hint="Typical (p50) and slow-case (p95) answer time." />
          <StatCard label="Cost / question" icon="coin" value={money(s.cost_per_case)} sub={`${money(s.cost_total)} total`} />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card p-5">
          <h2 className="h2 mb-4">Scorers</h2>
          <div className="space-y-4">
            {Object.entries(s.scorers).map(([k, v]) => (
              <div key={k}>
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5 font-mono text-xs text-white">{k}{help[k] && <Info text={help[k]} />}</span>
                  <span className="tabular-nums text-muted">{v.pass_rate == null ? "n/a" : pct(v.pass_rate, 0)} <span className="text-xs">· {v.n} scored</span></span>
                </div>
                <Bar value={v.pass_rate} />
              </div>
            ))}
          </div>
        </section>
        <section className="card p-5">
          <h2 className="h2 mb-4">By question group</h2>
          {tags.length === 0 ? <p className="text-sm text-muted">Give your test questions <code>tags</code> to see results per topic.</p> : (
            <div className="space-y-4">
              {tags.map(([t, v]) => (
                <div key={t}>
                  <div className="mb-1 flex justify-between text-sm"><span className="text-white">{t}</span><span className="tabular-nums text-muted">{pct(v.pass_rate, 0)} of {v.n}</span></div>
                  <Bar value={v.pass_rate} />
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {s.classification && <div className="card p-4 text-sm"><b className="text-white">Classification</b> · accuracy {pct(s.classification.accuracy)} · macro-F1 {s.classification.macro_f1.toFixed(3)}</div>}

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="h2">Questions</h2>
          <div className="flex overflow-hidden rounded-xl border border-line text-sm">
            {(["failing", "all"] as const).map((k) => <button key={k} onClick={() => setOnly(k)} className={`px-3.5 py-1.5 ${only === k ? "bg-accent/15 text-white" : "text-muted hover:text-white"}`}>{k === "failing" ? `Failing (${failing})` : `All (${run.results.length})`}</button>)}
          </div>
          <input className="input max-w-xs" placeholder="Search questions and answers…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {shown.length === 0 && <div className="card p-8 text-center text-sm text-muted">{failing === 0 && only === "failing" ? "Nothing failed in this run." : "No questions match."}</div>}
        {shown.slice(0, 200).map((r) => {
          const key = `${r.case_id}-${r.repeat}`;
          return <Row key={key} r={r} open={open === key} toggle={() => setOpen(open === key ? null : key)} />;
        })}
      </section>
    </div>
  );
}

function Row({ r, open, toggle }: { r: CaseResult; open: boolean; toggle: () => void }) {
  return (
    <div className={`card overflow-hidden border-l-4 ${r.passed ? "border-l-ok/70" : "border-l-bad/80"}`}>
      <button className="flex w-full items-center gap-3 p-3.5 text-left hover:bg-white/[0.03]" onClick={toggle} aria-expanded={open}>
        <Chip tone={r.passed ? "ok" : "bad"}>{r.passed ? "pass" : "fail"}</Chip>
        <span className="font-mono text-xs text-muted">{r.case_id}</span>
        <span className="min-w-0 flex-1 truncate text-sm text-white">{r.input}</span>
        <span className="hidden text-xs text-muted sm:block">{ms(r.output.latency_ms)}</span>
        <Icon name={open ? "up" : "down"} size={16} className="text-muted" />
      </button>
      {open && (
        <div className="space-y-4 border-t border-line/70 p-4">
          <ErrorBox error={r.output.error} />
          <div><span className="label">What the app answered</span><Code>{r.output.answer || "(empty)"}</Code></div>
          <div className="overflow-x-auto"><table className="w-full text-sm">
            <thead><tr><th>Scorer</th><th>Score</th><th>Result</th><th>Why</th></tr></thead>
            <tbody>
              {Object.entries(r.scores).map(([k, v]) => (
                <tr key={k} className="border-t border-line/60">
                  <td className="font-mono text-xs">{k}</td><td className="tabular-nums">{v.value == null ? "–" : v.value.toFixed(2)}</td>
                  <td>{v.passed == null ? <Chip>n/a</Chip> : <Chip tone={v.passed ? "ok" : "bad"}>{v.passed ? "pass" : "fail"}</Chip>}</td>
                  <td className="text-muted">{v.detail}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          {r.output.contexts.length > 0 && (
            <div><span className="label">Passages the app found ({r.output.contexts.length})</span>
              <div className="space-y-2">{r.output.contexts.map((c, i) => <Code key={i}>{`[${c.id ?? i}]${c.score != null ? `  score ${c.score}` : ""}\n${c.text.slice(0, 400)}`}</Code>)}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
