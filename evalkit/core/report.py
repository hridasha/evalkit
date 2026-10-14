"""Self-contained HTML reports (one file, no internet needed)."""

from __future__ import annotations

import datetime as dt
from html import escape

from evalkit.core.models import CaseResult

CSS = """
body{font:14px/1.5 system-ui,Segoe UI,sans-serif;background:#0b0d12;color:#e8ebf2;margin:0;padding:32px;max-width:1150px;margin:auto}
h1{margin:0 0 4px;font-size:24px}h2{margin:28px 0 8px;font-size:17px}.muted{color:#8b94a8}
.cards{display:flex;gap:12px;margin:18px 0;flex-wrap:wrap}.card{background:#141821;border:1px solid #262d3d;border-radius:12px;padding:12px 18px;min-width:120px}
.card b{display:block;font-size:24px}table{width:100%;border-collapse:collapse;margin-top:6px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #262d3d;vertical-align:top}th{color:#8b94a8;font-weight:500;font-size:12px;text-transform:uppercase}
.badge{padding:2px 9px;border-radius:99px;font-size:12px;font-weight:600}.ok{background:#12331f;color:#3ecf8e}.bad{background:#3d1818;color:#ff6b62}
.warn{background:#3d2a12;color:#f5a524}.mute{background:#2a2a2e;color:#a1a1aa}code{font:12.5px Consolas,monospace;background:#1b2130;padding:1px 6px;border-radius:5px}
.bar{height:8px;background:#262d3d;border-radius:99px;overflow:hidden;min-width:90px}.bar i{display:block;height:100%;background:#7c8cff}
details{background:#141821;border:1px solid #262d3d;border-radius:10px;padding:8px 12px;margin:6px 0}summary{cursor:pointer}
pre{white-space:pre-wrap;background:#0f1218;border-radius:8px;padding:10px;font:12.5px Consolas,monospace;margin:6px 0}
"""


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.1%}"


def _num(v: float | None, digits: int = 3) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def _ms(v: float | None) -> str:
    return "-" if v is None else f"{v:,.0f} ms"


def _badge(ok: bool | None, yes: str = "pass", no: str = "fail") -> str:
    if ok is None:
        return '<span class="badge mute">n/a</span>'
    return f'<span class="badge {"ok" if ok else "bad"}">{yes if ok else no}</span>'


def _page(title: str, body: str) -> str:
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>{CSS}</style><body>{body}</body></html>'


def _card(label: str, value: str) -> str:
    return f'<div class="card"><span class="muted">{escape(label)}</span><b>{escape(value)}</b></div>'


def _gate_html(gate: dict | None) -> str:
    if not gate:
        return ""
    rows = "".join(
        f"<tr><td>{_badge(c['ok'], 'ok', 'failed')}</td><td>{escape(c['name'])}</td><td>{escape(c['detail'])}</td></tr>"
        for c in gate["checks"]
    )
    head = "Quality gate: passed" if gate["passed"] else "Quality gate: FAILED"
    return f"<h2>{head}</h2><table><tr><th></th><th>Check</th><th>Result</th></tr>{rows}</table>"


def render_run(run: dict, results: list[CaseResult], gate: dict | None = None) -> str:
    s = run["summary"]
    when = dt.datetime.fromtimestamp(run["created_at"]).strftime("%Y-%m-%d %H:%M")
    lat = s.get("latency", {})
    cards = "".join([
        _card("Pass rate", _pct(s.get("pass_rate"))), _card("Questions", str(s.get("n", 0))), _card("Errors", str(s.get("errors", 0))),
        _card("p50 / p95", f"{_ms(lat.get('p50_ms'))} / {_ms(lat.get('p95_ms'))}"),
        _card("Cost / question", f"${s.get('cost_per_case', 0):.5f}"),
    ])  # fmt: skip
    scorer_rows = "".join(
        f"<tr><td><code>{escape(k)}</code></td><td>{_num(v['mean'])}</td><td>{_pct(v['pass_rate'])}</td>"
        f"<td><div class='bar'><i style='width:{(v['pass_rate'] or 0) * 100:.0f}%'></i></div></td><td class='muted'>{v['n']} scored, {v['skipped']} skipped</td></tr>"
        for k, v in s.get("scorers", {}).items()
    )
    tag_rows = "".join(
        f"<tr><td>{escape(t)}</td><td>{v['n']}</td><td>{_pct(v['pass_rate'])}</td></tr>"
        for t, v in s.get("by_tag", {}).items()
    )
    cls = s.get("classification")
    cls_html = ""
    if cls:
        per = "".join(
            f"<tr><td>{escape(k)}</td><td>{_num(v['precision'], 2)}</td><td>{_num(v['recall'], 2)}</td><td>{_num(v['f1'], 2)}</td><td>{v['support']}</td></tr>"
            for k, v in cls["per_label"].items()
        )
        cls_html = (f"<h2>Classification</h2><p>Accuracy {_pct(cls['accuracy'])}, macro-F1 {_num(cls['macro_f1'], 3)}</p>"
                    f"<table><tr><th>Label</th><th>Precision</th><th>Recall</th><th>F1</th><th>Cases</th></tr>{per}</table>")  # fmt: skip

    failing = [r for r in results if not r.passed]
    items = []
    for r in (failing + [r for r in results if r.passed])[:300]:
        scores = "".join(
            f"<tr><td><code>{escape(k)}</code></td><td>{_num(v.value)}</td><td>{_badge(v.passed)}</td><td class='muted'>{escape(v.detail)}</td></tr>"
            for k, v in r.scores.items()
        )
        ctx = "".join(f"<pre>[{escape(str(c.id))}] {escape(c.text[:400])}</pre>" for c in r.output.contexts[:4])
        err = f"<p><span class='badge bad'>error</span> {escape(r.output.error)}</p>" if r.output.error else ""
        items.append(
            f"<details{' open' if not r.passed and len(items) < 5 else ''}><summary>{_badge(r.passed)} <code>{escape(r.case_id)}</code> "
            f"{escape(r.input[:110])} <span class='muted'>{_ms(r.output.latency_ms)}</span></summary>{err}"
            f"<p class='muted'>Answer</p><pre>{escape(r.output.answer)}</pre><table>{scores}</table>"
            f"{'<p class=muted>Retrieved</p>' + ctx if ctx else ''}</details>"
        )
    body = (
        f"<h1>{escape(run['name'])} <span class='muted'>{escape(run['version'])}</span></h1>"
        f"<div class='muted'>Run #{run['id']} - {when} - {escape(run['status'])}</div><div class='cards'>{cards}</div>"
        f"{_gate_html(gate)}<h2>Scorers</h2><table><tr><th>Scorer</th><th>Mean</th><th>Pass rate</th><th></th><th></th></tr>{scorer_rows}</table>"
        f"{'<h2>By tag</h2><table><tr><th>Tag</th><th>Questions</th><th>Pass rate</th></tr>' + tag_rows + '</table>' if tag_rows else ''}"
        f"{cls_html}<h2>Questions ({len(failing)} failing)</h2>{''.join(items)}"
    )
    return _page(f"{run['name']} - EvalKit", body)


def render_compare(cmp: dict, a: dict, b: dict) -> str:
    pr = cmp["pass_rate"]
    kind = {"B is better": "ok", "A is better": "bad"}.get(pr["verdict"], "mute")
    rows = "".join(
        f"<tr><td><code>{escape(k)}</code></td><td>{_num(v['a'])}</td><td>{_num(v['b'])}</td><td>{v['diff']:+.3f}</td>"
        f"<td class='muted'>{v['ci'][0]:+.3f} to {v['ci'][1]:+.3f}</td><td>{v['p_value']:.3f}</td><td>{escape(v['verdict'])}</td></tr>"
        for k, v in cmp["scorers"].items()
    )
    lc = cmp["latency_cost"]
    body = (
        f"<h1>A/B comparison</h1><p class='muted'>A = run #{a['id']} {escape(a['version'])} &nbsp; B = run #{b['id']} {escape(b['version'])} &nbsp; ({cmp['n_paired']} questions in common)</p>"
        f"<p><span class='badge {kind}'>{escape(pr['verdict'])}</span> {escape(cmp['headline'])}</p>"
        f"<div class='cards'>{_card('Pass rate A', _pct(pr['a']))}{_card('Pass rate B', _pct(pr['b']))}{_card('Change', f'{pr["diff"]:+.1%}')}"
        f"{_card('p-value', f'{pr["p_value"]:.3f}')}</div>"
        f"<h2>Scorers</h2><table><tr><th>Scorer</th><th>A</th><th>B</th><th>Change</th><th>95% range</th><th>p</th><th>Verdict</th></tr>{rows}</table>"
        f"<h2>Speed and cost</h2><table><tr><th></th><th>p50</th><th>p95</th><th>Cost / question</th></tr>"
        f"<tr><td>A</td><td>{_ms(lc['a']['p50_ms'])}</td><td>{_ms(lc['a']['p95_ms'])}</td><td>${lc['a']['cost_per_case']:.5f}</td></tr>"
        f"<tr><td>B</td><td>{_ms(lc['b']['p50_ms'])}</td><td>{_ms(lc['b']['p95_ms'])}</td><td>${lc['b']['cost_per_case']:.5f}</td></tr></table>"
        f"<h2>Regressed ({len(cmp['regressed'])})</h2><p>{' '.join(f'<code>{escape(i)}</code>' for i in cmp['regressed']) or '<span class=muted>none</span>'}</p>"
        f"<h2>Improved ({len(cmp['improved'])})</h2><p>{' '.join(f'<code>{escape(i)}</code>' for i in cmp['improved']) or '<span class=muted>none</span>'}</p>"
    )
    return _page("A/B comparison - EvalKit", body)
