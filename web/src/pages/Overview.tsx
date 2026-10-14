import { useMemo } from "react";
import { Link } from "react-router-dom";
import { api, ms, money, pct, Run, when } from "../api";
import { Bar, Chip, Delta, Donut, EmptyState, ErrorBox, Icon, PageHeader, rateTone, Skeleton, Sparkline, StatCard, usePoll } from "../components/kit";

export default function Overview() {
  const { data: runs, error, loading } = usePoll(api.runs, 5000);
  const { data: sets } = usePoll(api.testsets, 8000);

  const done = useMemo(() => (runs ?? []).filter((r) => r.status === "done" && r.summary?.n), [runs]);
  const latest = done[0];
  const previous = latest ? done.find((r) => r.id !== latest.id && r.name === latest.name) : undefined;
  const trend = useMemo(() => (latest ? done.filter((r) => r.name === latest.name).slice(0, 12).reverse().map((r) => r.summary.pass_rate * 100) : []), [done, latest]);
  const weakTags = useMemo(() => (latest ? Object.entries(latest.summary.by_tag).sort((a, b) => a[1].pass_rate - b[1].pass_rate).slice(0, 4) : []), [latest]);

  const steps = [
    { done: (sets?.length ?? 0) > 0, title: "Add a test set", text: "A list of questions with the answers you expect.", to: "/testsets", cta: "Upload test set", icon: "list" },
    { done: done.length > 0, title: "Run an evaluation", text: "Point EvalKit at your app and score every answer.", to: "/new", cta: "Start a run", icon: "play" },
    { done: done.length > 1, title: "Compare two versions", text: "Find out if a change really made things better.", to: done.length > 1 ? `/compare?a=${done[1].id}&b=${done[0].id}` : "/new", cta: "Run it again", icon: "scale" },
  ];

  return (
    <div className="space-y-8">
      <PageHeader
        title="Is your AI app getting better?"
        subtitle="EvalKit asks your app a set of questions, scores every answer, and tells you whether a change helped or hurt."
        actions={<><Link to="/testsets" className="btn"><Icon name="upload" size={16} />Upload test set</Link><Link to="/new" className="btn btn-primary"><Icon name="play" size={16} />New run</Link></>}
      />
      <ErrorBox error={error} />
      {loading && <div className="grid gap-4 md:grid-cols-3"><Skeleton className="h-40" /><Skeleton className="h-40" /><Skeleton className="h-40" /></div>}

      {!loading && !latest && (
        <>
          <EmptyState icon="spark" title="Let's run your first evaluation" action={<Link to="/new" className="btn btn-primary">Start now <Icon name="arrow" size={16} /></Link>}>
            It takes about a minute: a demo app and a 37-question test set are already set up if you started EvalKit with Docker.
          </EmptyState>
          <Steps steps={steps} />
        </>
      )}

      {latest && (
        <>
          <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
            <Link to={`/runs/${latest.id}`} className="card card-hover flex flex-col items-center p-6 text-center">
              <span className="label">Latest run</span>
              <Donut value={latest.summary.pass_rate} size={150} stroke={13} label="pass rate" />
              <div className="mt-3 text-base font-semibold text-white">{latest.name} <span className="text-muted">{latest.version}</span></div>
              <div className="mt-1 flex items-center gap-2 text-xs text-muted">{when(latest.created_at)}</div>
              {previous && <div className="mt-2 flex items-center gap-1 text-xs text-muted">vs #{previous.id}: <Delta value={latest.summary.pass_rate - previous.summary.pass_rate} /></div>}
            </Link>

            <div className="grid gap-4 sm:grid-cols-2">
              <StatCard label="Questions" icon="list" value={latest.summary.n} sub={`${latest.summary.n - latest.summary.passed} failing`} tone={latest.summary.n === latest.summary.passed ? "ok" : undefined} />
              <StatCard label="Errors" icon="alert" value={latest.summary.errors} sub="app crashes or timeouts" tone={latest.summary.errors ? "bad" : "ok"} hint="Questions where your app did not answer (it crashed, timed out or sent no answer)." />
              <StatCard label="Speed (p95)" icon="runs" value={ms(latest.summary.latency.p95_ms)} sub={`typical ${ms(latest.summary.latency.p50_ms)}`} hint="95 out of 100 answers arrived at least this fast." />
              <StatCard label="Cost / question" icon="coin" value={money(latest.summary.cost_per_case)} sub={`${money(latest.summary.cost_total)} for this run`} />
              <div className="card flex items-center justify-between gap-4 p-4 sm:col-span-2">
                <div><span className="label">Pass rate over your last runs</span><div className="text-sm text-muted">{trend.length < 2 ? "Run again to see a trend." : `${trend.length} runs of ${latest.name}`}</div></div>
                <Sparkline values={trend} width={220} height={52} tone={rateTone(latest.summary.pass_rate)} />
              </div>
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="card p-5">
              <h2 className="h2 mb-1">Where it struggles most</h2>
              <p className="mb-4 text-xs text-muted">Question groups (tags) with the lowest pass rate in the latest run.</p>
              {weakTags.length === 0 && <p className="text-sm text-muted">Add tags to your test questions to see a breakdown here.</p>}
              <div className="space-y-3.5">
                {weakTags.map(([tag, v]) => (
                  <div key={tag}><div className="mb-1 flex justify-between text-sm"><span className="text-white">{tag}</span><span className="text-muted">{pct(v.pass_rate, 0)} of {v.n}</span></div><Bar value={v.pass_rate} /></div>
                ))}
              </div>
              <Link to={`/runs/${latest.id}`} className="mt-5 inline-flex items-center gap-1 text-sm text-accent hover:underline">See the failing questions <Icon name="arrow" size={14} /></Link>
            </section>

            <section className="card p-5">
              <div className="mb-3 flex items-center justify-between"><h2 className="h2">Recent runs</h2><Link to="/runs" className="text-xs text-accent hover:underline">View all</Link></div>
              <ul className="divide-y divide-line/70">
                {(runs ?? []).slice(0, 5).map((r: Run) => (
                  <li key={r.id}>
                    <Link to={`/runs/${r.id}`} className="flex items-center gap-3 rounded-lg px-1 py-2.5 hover:bg-white/5">
                      <span className="w-8 text-xs text-muted">#{r.id}</span>
                      <span className="min-w-0 flex-1 truncate text-sm text-white">{r.name} <span className="text-muted">{r.version}</span></span>
                      {r.status !== "done" ? <Chip tone="warn">{r.status}</Chip> : <Chip tone={rateTone(r.summary.pass_rate)}>{pct(r.summary.pass_rate, 0)}</Chip>}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          </div>

          {done.length < 2 && <Steps steps={steps} />}
        </>
      )}
    </div>
  );
}

function Steps({ steps }: { steps: { done: boolean; title: string; text: string; to: string; cta: string; icon: string }[] }) {
  return (
    <section>
      <h2 className="h2 mb-3">Getting started</h2>
      <div className="grid gap-4 md:grid-cols-3">
        {steps.map((s, i) => (
          <div key={s.title} className={`card p-5 ${s.done ? "border-ok/40" : ""}`}>
            <div className="mb-3 flex items-center gap-2">
              <span className={`grid h-7 w-7 place-items-center rounded-full text-xs font-bold ${s.done ? "bg-ok text-bg" : "bg-white/10 text-white"}`}>{s.done ? <Icon name="check" size={14} /> : i + 1}</span>
              <h3 className="font-medium text-white">{s.title}</h3>
            </div>
            <p className="mb-4 text-sm text-muted">{s.text}</p>
            <Link to={s.to} className={`btn ${s.done ? "btn-ghost text-ok" : "btn-primary"}`}>{s.done ? "Done · open" : s.cta}</Link>
          </div>
        ))}
      </div>
    </section>
  );
}
