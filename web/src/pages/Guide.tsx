import { Link } from "react-router-dom";
import { Icon, PageHeader } from "../components/kit";

const FLOW = [
  { icon: "list", title: "1. Your questions", text: "A test set: questions plus the answer you expect for each one." },
  { icon: "play", title: "2. Ask your app", text: "EvalKit sends every question to your app and records the answer, the passages it used, the speed and the cost." },
  { icon: "check", title: "3. Grade the answers", text: "Scorers check each answer: does it contain the right fact, did it find the right document, did an AI judge approve?" },
  { icon: "scale", title: "4. Compare and decide", text: "See the pass rate, find what broke, and test if a change really helped. A quality gate can stop a bad change in CI." },
];

const TERMS: [string, string][] = [
  ["Pass rate", "The share of questions where every scorer was happy."],
  ["Scorer", "A small checker that grades one answer (e.g. contains, hit_rate, judge). You choose which to use."],
  ["Hit rate / MRR", "Did the app's search find a right document in its top results, and how high did it rank it?"],
  ["Faithfulness", "Does the answer only say things the retrieved passages support? Unsupported claims are made up."],
  ["Abstain", "Saying “I don't know” when the documents have no answer. A good app does it at the right time, not always."],
  ["LLM judge", "A language model that grades answers and explains why. Helpful, but it needs a human spot-check."],
  ["95% range", "With few questions the true pass rate could be a bit higher or lower. This is the believable range."],
  ["p-value", "The chance of a gap this big by luck alone if nothing changed. Under 0.05 is usually called a real difference."],
  ["Quality gate", "Rules such as “pass rate at least 70%”. If they fail, the command exits with 1 and your CI build fails."],
  ["p50 / p95", "Typical and slow-case answer time. p95 means 95 of 100 answers were at least this fast."],
];

export default function Guide() {
  return (
    <div className="space-y-8">
      <PageHeader title="How EvalKit works" subtitle="Think of it as an exam for your AI app: questions with known answers, a marker for every answer, and a grade you can track over time." />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {FLOW.map((s) => (
          <div key={s.title} className="card p-5">
            <span className="mb-3 grid h-10 w-10 place-items-center rounded-xl bg-accent/10 text-accent"><Icon name={s.icon} size={20} /></span>
            <h2 className="h2">{s.title}</h2>
            <p className="mt-1.5 text-sm text-muted">{s.text}</p>
          </div>
        ))}
      </div>

      <section className="card p-6">
        <h2 className="h2 mb-3">A concrete example</h2>
        <div className="space-y-3 text-sm leading-relaxed text-slate-300">
          <p>Your support bot answers questions about refunds. You have a test set with the question <b className="text-white">“How many days do I have to return an item?”</b> and the expected answer <b className="text-white">“30 days”</b>.</p>
          <p>EvalKit asks the bot, gets an answer, and the <code>contains</code> scorer checks that “30 days” is in it. Another scorer checks that the refunds document was among the passages the bot found. If the bot says “14 days” instead, the question fails, and you see exactly what it said and why.</p>
          <p>You change the bot's prompt and run again. EvalKit lines the two runs up question by question, and tells you whether the new version is <b className="text-white">better</b>, <b className="text-white">worse</b>, or <b className="text-white">no clear difference</b> (so you don't celebrate luck).</p>
        </div>
      </section>

      <section>
        <h2 className="h2 mb-3">Where to click</h2>
        <div className="grid gap-4 md:grid-cols-3">
          {[
            { to: "/testsets", t: "Test sets", d: "Upload or view your questions." },
            { to: "/new", t: "New run", d: "Point EvalKit at your app and start." },
            { to: "/runs", t: "Runs", d: "Every evaluation. Tick two to compare." },
          ].map((l) => (
            <Link key={l.to} to={l.to} className="card card-hover flex items-center justify-between p-4">
              <div><div className="font-medium text-white">{l.t}</div><div className="text-sm text-muted">{l.d}</div></div>
              <Icon name="arrow" className="text-accent" />
            </Link>
          ))}
        </div>
      </section>

      <section className="card overflow-hidden">
        <h2 className="h2 border-b border-line/70 px-5 py-3">Words you will see</h2>
        <dl className="divide-y divide-line/60">
          {TERMS.map(([t, d]) => (
            <div key={t} className="grid gap-1 px-5 py-3 sm:grid-cols-[180px_1fr]"><dt className="font-medium text-white">{t}</dt><dd className="text-sm text-muted">{d}</dd></div>
          ))}
        </dl>
      </section>
    </div>
  );
}
