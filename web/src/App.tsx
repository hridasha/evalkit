import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Chip, Icon, ToastProvider } from "./components/kit";
import Compare from "./pages/Compare";
import Connect from "./pages/Connect";
import Guide from "./pages/Guide";
import NewRun from "./pages/NewRun";
import Overview from "./pages/Overview";
import Profile from "./pages/Profile";
import RunDetail from "./pages/RunDetail";
import Runs from "./pages/Runs";
import TestSets from "./pages/TestSets";

const NAV: { to: string; label: string; icon: string; end?: boolean; hint: string }[] = [
  { to: "/", label: "Overview", icon: "home", end: true, hint: "How your app is doing" },
  { to: "/runs", label: "Runs", icon: "runs", hint: "Every evaluation you ran" },
  { to: "/connect", label: "Connect app", icon: "external", hint: "Link EvalKit to your app" },
  { to: "/new", label: "New run", icon: "play", hint: "Test your app now" },
  { to: "/testsets", label: "Test sets", icon: "list", hint: "Your questions" },
  { to: "/profile", label: "Cost profile", icon: "coin", hint: "Cheapest good setup" },
  { to: "/guide", label: "How it works", icon: "info", hint: "Plain-language guide" },
];

const linkCls = ({ isActive }: { isActive: boolean }) =>
  `group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${
    isActive ? "bg-accent/15 text-white shadow-inner shadow-accent/10" : "text-muted hover:bg-white/5 hover:text-white"
  }`;

export default function App() {
  const [open, setOpen] = useState(false);
  const [llm, setLlm] = useState<boolean | null>(null);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => { fetch("/api/health").then((r) => r.json()).then((h) => setLlm(!!h.llm_ready)).catch(() => setLlm(null)); }, []);

  return (
    <ToastProvider>
      <div className="min-h-screen lg:grid lg:grid-cols-[250px_1fr]">
        {/* mobile top bar */}
        <div className="sticky top-0 z-40 flex items-center justify-between border-b border-line bg-bg/90 px-4 py-3 backdrop-blur lg:hidden">
          <Brand />
          <button className="btn btn-ghost !p-2" onClick={() => setOpen(!open)} aria-label="Menu"><Icon name={open ? "x" : "menu"} /></button>
        </div>

        <aside className={`${open ? "block" : "hidden"} fixed inset-x-0 top-[57px] z-30 border-b border-line bg-bg/95 p-4 backdrop-blur lg:sticky lg:top-0 lg:block lg:h-screen lg:border-b-0 lg:border-r lg:bg-transparent lg:p-5`}>
          <div className="hidden lg:block"><Brand /></div>
          <nav className="mt-2 space-y-1 lg:mt-8">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={linkCls} title={n.hint}>
                <Icon name={n.icon} size={18} />{n.label}
              </NavLink>
            ))}
          </nav>
          <div className="mt-6 space-y-2 rounded-2xl border border-line/80 bg-panel/60 p-3.5 text-xs lg:absolute lg:bottom-5 lg:left-5 lg:right-5 lg:mt-0">
            <div className="flex items-center justify-between"><span className="text-muted">LLM judge</span>
              {llm === null ? <Chip>unknown</Chip> : llm ? <Chip tone="ok">Gemini key set</Chip> : <Chip tone="warn">no key</Chip>}</div>
            {llm === false && <p className="leading-relaxed text-muted">Rule-based scorers work without it. Add a key to use the judge and test-set generator.</p>}
            <a className="inline-flex items-center gap-1 text-accent hover:underline" href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">Get a Gemini key <Icon name="external" size={12} /></a>
          </div>
        </aside>

        <main className="min-w-0 px-4 pb-24 pt-6 sm:px-8 lg:px-10 lg:pt-8">
          <div className="mx-auto max-w-6xl animate-slide-up">
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/runs" element={<Runs />} />
              <Route path="/runs/:id" element={<RunDetail />} />
              <Route path="/compare" element={<Compare />} />
              <Route path="/connect" element={<Connect />} />
              <Route path="/new" element={<NewRun />} />
              <Route path="/testsets" element={<TestSets />} />
              <Route path="/profile" element={<Profile />} />
              <Route path="/guide" element={<Guide />} />
              <Route path="*" element={<p className="text-muted">Nothing here. <NavLink className="text-accent" to="/">Go to the overview</NavLink></p>} />
            </Routes>
          </div>
        </main>
      </div>
    </ToastProvider>
  );
}

function Brand() {
  return (
    <NavLink to="/" className="flex items-center gap-2.5 text-lg font-semibold tracking-tight text-white">
      <span className="grid h-8 w-8 place-items-center rounded-xl bg-gradient-to-br from-[#9aa6ff] to-[#6b7bf5] shadow-lg shadow-accent/30">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0b0d12" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
      </span>
      EvalKit
    </NavLink>
  );
}
