import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Chip, Code, ErrorBox, Field, Icon, Info, PageHeader, Warning, useToast } from "../components/kit";

type Preset = { name: string; label: string; description: string; model: string; base_url: string; api_key_env: string };
type Probe = {
  ok: boolean; error: string | null; latency_ms?: number; answer?: string; reply?: string; toml?: string;
  contexts?: { id: string | null; text: string }[]; env_vars?: { name: string; set: boolean }[];
  suggestions?: { answer_paths: { path: string; preview: string }[] } | null; target?: Record<string, unknown>;
};

const DEFAULT_CURL = `curl https://api.openai.com/v1/chat/completions \\
  -H "Authorization: Bearer sk-your-key" \\
  -H "Content-Type: application/json" \\
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "What is the refund policy?"}]}'`;

const TABS = [
  { id: "curl", label: "Paste a cURL command", hint: "Copy it from API docs, Postman or the browser's Network tab." },
  { id: "preset", label: "Pick a service", hint: "OpenAI, Claude, Gemini, Ollama, Groq and more: just choose and add a model." },
  { id: "url", label: "My own API", hint: "An endpoint that takes a question and replies with JSON." },
] as const;

export default function Connect() {
  const nav = useNavigate();
  const toast = useToast();
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("curl");
  const [presets, setPresets] = useState<Preset[]>([]);
  const [curl, setCurl] = useState(DEFAULT_CURL);
  const [sample, setSample] = useState("");
  const [preset, setPreset] = useState("openai");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [url, setUrl] = useState("http://localhost:8000/ask");
  const [question, setQuestion] = useState("What is 2 + 2?");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Probe | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { fetch("/api/presets").then((r) => r.json()).then(setPresets).catch(() => {}); }, []);
  const current = presets.find((p) => p.name === preset);

  async function test() {
    setBusy(true); setErr(null); setRes(null);
    const body: Record<string, unknown> = { question };
    if (tab === "curl") { body.curl = curl; if (sample.trim()) body.sample = sample.trim(); }
    else if (tab === "preset") body.target = { preset, ...(model ? { model } : {}), ...(baseUrl ? { base_url: baseUrl } : {}) };
    else body.target = { url, contexts_path: "auto", tokens_in_path: "auto", tokens_out_path: "auto" };
    if (key.trim()) {
      const envName = (current?.api_key_env || "API_KEY");
      body.secrets = { [envName]: key.trim(), API_KEY: key.trim(), ...(tab === "curl" ? {} : {}) };
    }
    try {
      const r = await fetch("/api/connect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail));
      setRes(j);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  function useIt() {
    if (!res?.toml) return;
    const cfg = `name = "my-app"\n\n${res.toml}\n[run]\nconcurrency = 4\n\n[[scorers]]\nname = "contains"\n\n[gate]\nmin_pass_rate = 0.7\nmax_errors = 0\nmax_regression = 0.05\n`;
    try { localStorage.setItem("evalkit.config", cfg); localStorage.setItem("evalkit.saved", "1"); } catch { /* ignore */ }
    toast("Connection saved to New run");
    nav("/new");
  }

  const missing = res?.env_vars?.filter((v) => !v.set) ?? [];
  return (
    <div className="space-y-6">
      <PageHeader title="Connect your app" subtitle="Tell EvalKit how to reach your app. It sends one test question, shows what came back, and works out the settings, so you never write an adapter." />

      <div className="grid gap-3 md:grid-cols-3">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => { setTab(t.id); setRes(null); setErr(null); }} className={`card p-4 text-left transition ${tab === t.id ? "!border-accent bg-accent/10" : "card-hover"}`}>
            <div className="font-medium text-white">{t.label}</div><div className="mt-1 text-xs text-muted">{t.hint}</div>
          </button>
        ))}
      </div>

      <section className="card space-y-4 p-5">
        {tab === "curl" && (
          <>
            <Field label="cURL command" hint="Real keys are never saved: EvalKit swaps them for an environment variable name.">
              <textarea className="input h-36 font-mono text-xs" spellCheck={false} value={curl} onChange={(e) => setCurl(e.target.value)} />
            </Field>
            <Field label="Question text inside the command (optional)" hint="Only needed if EvalKit guesses the wrong part. Leave empty to auto-detect."><input className="input" value={sample} onChange={(e) => setSample(e.target.value)} placeholder="What is the refund policy?" /></Field>
          </>
        )}
        {tab === "preset" && (
          <div className="grid gap-4 md:grid-cols-3">
            <Field label="Service" hint={current?.description}>
              <select className="input" value={preset} onChange={(e) => { setPreset(e.target.value); setModel(""); setBaseUrl(""); }}>{presets.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}</select>
            </Field>
            <Field label="Model" hint="Leave empty for the default."><input className="input" value={model} onChange={(e) => setModel(e.target.value)} placeholder={current?.model} /></Field>
            <Field label="Address" hint="Change only for your own server."><input className="input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={current?.base_url} /></Field>
          </div>
        )}
        {tab === "url" && (
          <Field label="Endpoint" hint='Sends {"question": "..."} and looks for the answer in the JSON reply (any shape).'><input className="input" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
        )}
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Test question"><input className="input" value={question} onChange={(e) => setQuestion(e.target.value)} /></Field>
          {tab !== "url" && <Field label="API key (for this test only)" hint="Not stored. For real runs, set the environment variable shown below."><input className="input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="optional" /></Field>}
        </div>
        <ErrorBox error={err} />
        <button className="btn btn-primary" onClick={test} disabled={busy}><Icon name="play" size={16} />{busy ? "Testing…" : "Test connection"}</button>
      </section>

      {res && (
        <section className="card animate-slide-up space-y-4 p-5">
          <div className="flex flex-wrap items-center gap-3">
            <Chip tone={res.ok ? "ok" : "bad"}>{res.ok ? "Connected" : "Failed"}</Chip>
            {res.latency_ms != null && <span className="text-sm text-muted">{Math.round(res.latency_ms)} ms</span>}
          </div>
          <ErrorBox error={res.error} />
          {missing.length > 0 && (
            <Warning>This request needs the environment variable{missing.length > 1 ? "s" : ""} <b>{missing.map((m) => m.name).join(", ")}</b>. Add {missing.length > 1 ? "them" : "it"} to your <code>.env</code> file (Docker) or your shell before running. {!res.ok && "Or paste a key above to test now."}</Warning>
          )}
          {res.ok && (
            <>
              <div><span className="label flex items-center gap-1">The answer EvalKit read<Info text="If this is not the answer, pick the right field below." /></span><Code>{res.answer}</Code></div>
              {res.suggestions && res.suggestions.answer_paths.length > 1 && (
                <div><span className="label">Other places an answer might be</span>
                  <div className="flex flex-wrap gap-2">{res.suggestions.answer_paths.slice(0, 4).map((s) => <span key={s.path} className="rounded-lg border border-line px-2 py-1 text-xs"><code className="text-accent">{s.path || "(whole reply)"}</code> <span className="text-muted">{s.preview.slice(0, 40)}</span></span>)}</div>
                </div>
              )}
              {res.contexts && res.contexts.length > 0 && <p className="text-sm text-muted">Also found <b className="text-white">{res.contexts.length}</b> retrieved passage(s), so retrieval scorers will work.</p>}
              <div><span className="label">Settings to use (saved for you in New run)</span><Code>{res.toml}</Code></div>
              <button className="btn btn-primary" onClick={useIt}>Use this connection <Icon name="arrow" size={16} /></button>
            </>
          )}
          {res.reply && <details className="text-sm"><summary className="cursor-pointer text-muted">Raw reply from your app</summary><div className="mt-2"><Code>{res.reply}</Code></div></details>}
        </section>
      )}
    </div>
  );
}
