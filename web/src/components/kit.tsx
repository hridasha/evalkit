import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { Link } from "react-router-dom";

/* ---------- icons (tiny inline set, no dependency) ---------- */
const PATHS: Record<string, string> = {
  home: "M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10",
  runs: "M4 19V9m6 10V5m6 14v-7m6 7H2",
  play: "M7 5v14l12-7z",
  flask: "M9 3h6M10 3v6L4.5 19a1.5 1.5 0 001.3 2.2h12.4a1.5 1.5 0 001.3-2.2L14 9V3",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  coin: "M12 2a10 10 0 100 20 10 10 0 000-20zm0 5v10m-3-7.5c0-1 1.3-1.5 3-1.5s3 .7 3 2-1.3 1.8-3 2-3 .8-3 2 1.3 2 3 2 3-.5 3-1.5",
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12M18 6L6 18",
  up: "M7 14l5-5 5 5",
  down: "M7 10l5 5 5-5",
  arrow: "M5 12h14m-6-6l6 6-6 6",
  upload: "M12 16V4m0 0l-4 4m4-4l4 4M4 20h16",
  scale: "M12 3v18M5 7h14M5 7l-3 7a3 3 0 006 0L5 7zm14 0l-3 7a3 3 0 006 0l-3-7z",
  spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
  alert: "M12 9v4m0 4h.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  menu: "M4 7h16M4 12h16M4 17h16",
  info: "M12 8h.01M11 12h1v5h1m-1-15a10 10 0 100 20 10 10 0 000-20z",
  external: "M14 4h6v6m0-6L10 14M20 14v6H4V4h6",
  trash: "M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3",
};

export function Icon({ name, size = 18, className = "" }: { name: keyof typeof PATHS | string; size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d={PATHS[name] ?? PATHS.info} />
    </svg>
  );
}

/* ---------- tone helpers ---------- */
export type Tone = "ok" | "warn" | "bad" | "mute" | "accent";
export const rateTone = (v: number | null | undefined): Tone => (v == null ? "mute" : v >= 0.85 ? "ok" : v >= 0.6 ? "warn" : "bad");
export const toneText: Record<Tone, string> = { ok: "text-ok", warn: "text-warn", bad: "text-bad", mute: "text-muted", accent: "text-accent" };
export const toneBg: Record<Tone, string> = { ok: "bg-ok", warn: "bg-warn", bad: "bg-bad", mute: "bg-slate-500", accent: "bg-accent" };
const toneHex: Record<Tone, string> = { ok: "#3ecf8e", warn: "#f5a524", bad: "#ff6b62", mute: "#64748b", accent: "#7c8cff" };
const toneChip: Record<Tone, string> = {
  ok: "bg-green-500/15 text-ok", warn: "bg-amber-500/15 text-warn", bad: "bg-red-500/15 text-bad", mute: "bg-slate-500/15 text-slate-400", accent: "bg-indigo-500/15 text-accent",
};

export function Chip({ tone = "mute", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge ${toneChip[tone]}`}>{children}</span>;
}

/* ---------- visual pieces ---------- */
export function Donut({ value, size = 120, stroke = 11, label }: { value: number | null; size?: number; stroke?: number; label?: string }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value ?? 0));
  const tone = rateTone(value);
  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#232b3c" strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={toneHex[tone]} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={`${c * v} ${c}`} style={{ transition: "stroke-dasharray .6s ease" }} />
      </svg>
      <div className="absolute text-center">
        <div className={`font-semibold tabular-nums ${toneText[tone]}`} style={{ fontSize: size * 0.24 }}>{value == null ? "–" : `${Math.round(v * 100)}%`}</div>
        {label && <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>}
      </div>
    </div>
  );
}

export function Sparkline({ values, width = 120, height = 36, tone = "accent" }: { values: number[]; width?: number; height?: number; tone?: Tone }) {
  if (values.length < 2) return <div className="text-xs text-muted">needs 2+ runs</div>;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * (width - 6) + 3, height - 4 - ((v - min) / span) * (height - 8)]);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg width={width} height={height} aria-hidden>
      <path d={`${d} L${last[0]},${height} L${pts[0][0]},${height} Z`} fill={toneHex[tone]} opacity="0.12" />
      <path d={d} fill="none" stroke={toneHex[tone]} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="3" fill={toneHex[tone]} />
    </svg>
  );
}

export function Bar({ value, tone, className = "" }: { value: number | null; tone?: Tone; className?: string }) {
  const t = tone ?? rateTone(value);
  return (
    <div className={`h-2 overflow-hidden rounded-full bg-line ${className}`}>
      <div className={`h-full rounded-full ${toneBg[t]} transition-all duration-500`} style={{ width: `${Math.max(0, Math.min(1, value ?? 0)) * 100}%` }} />
    </div>
  );
}

export function Delta({ value, suffix = " pts" }: { value: number | null; suffix?: string }) {
  if (value == null) return null;
  const pts = value * 100;
  if (Math.abs(pts) < 0.05) return <span className="text-xs text-muted">no change</span>;
  const up = pts > 0;
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${up ? "text-ok" : "text-bad"}`}>
      <Icon name={up ? "up" : "down"} size={14} />{Math.abs(pts).toFixed(1)}{suffix}
    </span>
  );
}

export function StatCard({ label, value, sub, icon, tone, hint }: { label: string; value: ReactNode; sub?: ReactNode; icon?: string; tone?: Tone; hint?: string }) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <span className="label mb-0 flex items-center gap-1">{label}{hint && <Info text={hint} />}</span>
        {icon && <span className={`grid h-7 w-7 place-items-center rounded-lg bg-white/5 ${tone ? toneText[tone] : "text-muted"}`}><Icon name={icon} size={15} /></span>}
      </div>
      <div className={`mt-2 text-2xl font-semibold tabular-nums ${tone ? toneText[tone] : "text-white"}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function Info({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex cursor-help normal-case tracking-normal">
      <Icon name="info" size={13} className="text-muted/70 group-hover:text-accent" />
      <span role="tooltip" className="pointer-events-none absolute left-1/2 top-full z-30 mt-1.5 hidden w-60 -translate-x-1/2 rounded-lg border border-line bg-[#0d1119] p-2.5 text-xs font-normal leading-relaxed text-slate-300 shadow-xl group-hover:block">{text}</span>
    </span>
  );
}

export function PageHeader({ title, subtitle, actions, back }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; back?: { to: string; label: string } }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {back && <Link to={back.to} className="mb-1 inline-flex items-center gap-1 text-xs text-muted hover:text-white">← {back.label}</Link>}
        <h1 className="h1 truncate">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({ icon = "spark", title, children, action }: { icon?: string; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center px-6 py-12 text-center">
      <span className="mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-accent/10 text-accent"><Icon name={icon} size={24} /></span>
      <h3 className="h2">{title}</h3>
      {children && <p className="mt-1 max-w-md text-sm text-muted">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Skeleton({ className = "h-24" }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}

export function ErrorBox({ error }: { error: string | null }) {
  return error ? (
    <div className="flex items-start gap-2 rounded-xl border border-bad/40 bg-red-500/10 px-3.5 py-2.5 text-sm text-bad"><Icon name="alert" size={16} className="mt-0.5 shrink-0" /><span>{error}</span></div>
  ) : null;
}

export function Warning({ children }: { children: ReactNode }) {
  return <div className="flex items-start gap-2 rounded-xl border border-warn/40 bg-amber-500/10 px-3.5 py-2.5 text-sm text-warn"><Icon name="alert" size={16} className="mt-0.5 shrink-0" /><div>{children}</div></div>;
}

/* ---------- toasts ---------- */
type Toast = { id: number; text: string; kind: "ok" | "bad" };
const ToastCtx = createContext<(text: string, kind?: "ok" | "bad") => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: "ok" | "bad" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, kind }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 3800);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className={`pointer-events-auto flex animate-slide-up items-center gap-2 rounded-xl border px-4 py-2.5 text-sm shadow-xl ${t.kind === "ok" ? "border-ok/40 bg-[#0e1a16] text-ok" : "border-bad/40 bg-[#1c1112] text-bad"}`}>
            <Icon name={t.kind === "ok" ? "check" : "alert"} size={16} />{t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---------- polling + misc ---------- */
export function usePoll<T>(fn: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    const run = () =>
      fn().then((d) => live && (setData(d), setError(null), setLoading(false))).catch((e) => live && (setError(String(e.message ?? e)), setLoading(false)));
    run();
    const t = setInterval(run, ms);
    return () => { live = false; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, reload: () => fn().then(setData).catch((e) => setError(String(e.message ?? e))) };
}

export function Code({ children }: { children: ReactNode }) {
  return <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-bg/80 p-3 font-mono text-xs leading-relaxed text-slate-300">{children}</pre>;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}
