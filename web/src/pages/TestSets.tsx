import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, TestCase } from "../api";
import { Chip, EmptyState, ErrorBox, Icon, PageHeader, Skeleton, useToast, usePoll } from "../components/kit";

const SAMPLE = `{"id": "q1", "input": "How long do refunds take?", "expected": "5 to 7 business days", "relevant": ["refunds"], "tags": ["refunds"]}
{"id": "q2", "input": "Do you sell tents on the moon?", "should_abstain": true, "tags": ["unanswerable"]}`;

export default function TestSets() {
  const { data: sets, error, loading, reload } = usePoll(api.testsets, 5000);
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [fileName, setFileName] = useState("");
  const [drag, setDrag] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<{ name: string; cases: TestCase[] } | null>(null);

  async function upload(f?: File) {
    const chosen = f ?? file.current?.files?.[0];
    if (!chosen) return setErr("Choose a .jsonl or .csv file first.");
    setErr(null);
    try {
      const r = await api.uploadTestset(chosen, name.trim());
      toast(`Saved "${r.name}" with ${r.count} questions`);
      setName(""); setFileName("");
      if (file.current) file.current.value = "";
      await reload();
    } catch (e) { setErr((e as Error).message); }
  }
  async function open(n: string) { try { setView({ name: n, cases: await api.testset(n) }); } catch (e) { setErr((e as Error).message); } }
  async function remove(n: string) {
    if (!window.confirm(`Delete the test set "${n}"?`)) return;
    await api.deleteTestset(n); setView(null); await reload(); toast(`Deleted "${n}"`);
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Test sets" subtitle="A test set is a list of questions, each with the answer you expect. Write one by hand, or let EvalKit generate one from your documents." />

      <section className="card p-5">
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f) { setFileName(f.name); upload(f); } }}
          className={`flex flex-col items-center gap-2 rounded-2xl border-2 border-dashed px-6 py-8 text-center transition ${drag ? "border-accent bg-accent/10" : "border-line"}`}>
          <span className="grid h-11 w-11 place-items-center rounded-2xl bg-accent/10 text-accent"><Icon name="upload" size={22} /></span>
          <p className="text-sm text-white">Drop a <code>.jsonl</code> or <code>.csv</code> file here</p>
          <p className="text-xs text-muted">or</p>
          <div className="flex flex-wrap items-center justify-center gap-2">
            <input ref={file} type="file" accept=".jsonl,.csv,.json" className="hidden" id="ts-file" onChange={(e) => setFileName(e.target.files?.[0]?.name ?? "")} />
            <label htmlFor="ts-file" className="btn cursor-pointer">Choose file</label>
            {fileName && <span className="text-sm text-slate-300">{fileName}</span>}
            <input className="input !w-44" value={name} onChange={(e) => setName(e.target.value)} placeholder="name (optional)" aria-label="Test set name" />
            <button className="btn btn-primary" onClick={() => upload()}>Upload</button>
          </div>
        </div>
        <details className="mt-4 text-sm text-muted">
          <summary className="cursor-pointer select-none text-slate-300">What should the file look like?</summary>
          <p className="mt-2">One JSON object per line. <code>expected</code> is the right answer, <code>relevant</code> lists the document ids that hold it, <code>should_abstain</code> marks questions the app should answer with “I don’t know”.</p>
          <pre className="mt-2 overflow-auto rounded-xl bg-bg/80 p-3 font-mono text-xs text-slate-300">{SAMPLE}</pre>
        </details>
        <div className="mt-3"><ErrorBox error={err ?? error} /></div>
      </section>

      {loading && <Skeleton className="h-32" />}
      {sets && sets.length === 0 && <EmptyState icon="list" title="No test sets yet">Upload your first file above.</EmptyState>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {sets?.map((s) => (
          <div key={s.name} className={`card card-hover p-5 ${view?.name === s.name ? "!border-accent" : ""}`}>
            <h3 className="truncate font-medium text-white">{s.name}</h3>
            <p className="mt-2 text-3xl font-semibold tabular-nums text-white">{s.count} <span className="text-sm font-normal text-muted">questions</span></p>
            <div className="mt-4 flex gap-2"><button className="btn" onClick={() => open(s.name)}>View</button><Link className="btn btn-primary" to={`/new?testset=${encodeURIComponent(s.name)}`}><Icon name="play" size={14} />Run</Link><button className="btn btn-ghost btn-danger ml-auto !px-2" title="Delete" onClick={() => remove(s.name)}><Icon name="trash" size={16} /></button></div>
          </div>
        ))}
      </div>

      {view && (
        <section className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-line/70 px-5 py-3"><h2 className="h2">{view.name}</h2><button className="btn btn-ghost !px-2" onClick={() => setView(null)} aria-label="Close"><Icon name="x" size={16} /></button></div>
          <div className="max-h-[28rem] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-panel"><tr><th>Id</th><th>Question</th><th>Expected</th><th>Tags</th></tr></thead>
              <tbody>
                {view.cases.map((c) => (
                  <tr key={c.id} className="border-t border-line/60">
                    <td className="font-mono text-xs text-muted">{c.id}</td><td>{c.input}</td>
                    <td>{c.should_abstain ? <Chip tone="warn">should say “I don’t know”</Chip> : <span className="text-slate-300">{c.expected}</span>}</td>
                    <td className="text-muted">{c.tags.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
