"""Step 9: the Learn Report, a self-contained HTML file in the session folder."""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any

from jamcrammer.app.theme import PALETTE

MAX_INLINE_THUMBS = 60


def _img(session_dir: Path, rel: str, width: int = 320) -> str:
    path = session_dir / rel
    if not path.exists():
        return ""
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<img src="data:image/png;base64,{data}" width="{width}" alt="{html.escape(rel)}">'


def render_report(session_dir: Path, result: dict[str, Any] | None = None) -> Path:
    if result is None:
        result = json.loads((session_dir / "analysis" / "result.json").read_text(encoding="utf-8"))
    p = PALETTE
    css = f"""
    body {{ background:{p.bg}; color:{p.text}; font-family: 'Segoe UI Variable','Segoe UI',sans-serif; margin:0; padding:24px; }}
    h1 {{ font-size:28px; margin:0 0 4px 0; }} h2 {{ font-size:18px; margin:28px 0 8px 0; }}
    .muted {{ color:{p.muted}; }} .card {{ background:{p.surface}; border:1px solid {p.border}; border-radius:12px; padding:16px; margin:12px 0; }}
    table {{ border-collapse:collapse; width:100%; }} th {{ text-align:left; color:{p.muted}; font-size:12px; text-transform:uppercase; padding:6px 8px; border-bottom:1px solid {p.border}; }}
    td {{ padding:6px 8px; border-bottom:1px solid {p.border}; vertical-align:top; }}
    .chip {{ display:inline-block; border-radius:9px; padding:2px 8px; font-size:12px; font-weight:600; color:{p.bg}; }}
    .ok {{ background:{p.ok}; }} .warn {{ background:{p.warn}; }} .bad {{ background:{p.danger}; }}
    .states {{ display:flex; flex-wrap:wrap; gap:12px; }} .state {{ background:{p.raised}; border-radius:8px; padding:8px; width:340px; }}
    .strip {{ display:flex; gap:2px; overflow-x:auto; padding:8px 0; }} .strip div {{ height:28px; min-width:4px; border-radius:2px; }}
    img {{ border-radius:6px; display:block; }}
    """
    colors = [p.bestguess, p.arcade, p.opportunist, p.warn, p.danger, p.text, p.muted]
    parts = [
        f"<!doctype html><html><head><meta charset='utf-8'><title>Learn Report {html.escape(result['session'])}</title><style>{css}</style></head><body>"
    ]
    parts.append(
        f"<h1>Learn Report · {html.escape(result['session'])}</h1><div class='muted'>target <b>{html.escape(result['target'])}</b> · {result['frames']} frames ({result['unique_frames']} unique) · {len(result['states'])} states · {result['transitions']} transitions · generated {result['generated']} in {result['elapsed_s']} s</div>"
    )

    # states
    parts.append("<h2>States</h2><div class='states'>")
    for s in result["states"]:
        tags = " ".join(
            f"<span class='chip warn'>{html.escape(t)}</span>" for t in s.get("tags", [])
        )
        parts.append(
            f"<div class='state'><div><b>state {s['id']}</b> <span class='muted'>{s['frames']} frames · median {s['median_duration_s']} s</span> {tags}</div>{_img(session_dir, s['representative'])}</div>"
        )
    parts.append("</div>")

    # timeline strip
    labels_path = session_dir / "analysis" / "labels.json"
    if labels_path.exists():
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        parts.append("<h2>Timeline</h2><div class='card'><div class='strip'>")
        for lab in labels:
            parts.append(
                f"<div style='background:{colors[int(lab['state']) % len(colors)]}' title='{lab['ts']:.1f}s state {lab['state']}'></div>"
            )
        parts.append(
            "</div><div class='muted'>one block per keyframe, colored by state · cycle: "
            + (" → ".join(str(c) for c in result["cycle"]) or "not found")
            + "</div></div>"
        )

    # regions
    parts.append(
        "<h2>Text regions</h2><div class='card'><table><tr><th>kind</th><th>box (stage px)</th><th>frames</th><th>distinct</th><th>conf</th><th>samples</th></tr>"
    )
    for r in result["regions"]:
        cls = "ok" if r["kind"] in ("fact", "countdown", "gem_counter") else "warn"
        parts.append(
            f"<tr><td><span class='chip {cls}'>{html.escape(r['kind'])}</span></td><td>{r['box']}</td><td>{r['frames']}</td><td>{r['distinct']}</td><td>{r['confidence']}</td><td>{html.escape(' | '.join(r['samples']))[:200]}</td></tr>"
        )
    parts.append("</table></div>")

    # zones and proposal
    parts.append(
        "<h2>Proposed profile</h2><div class='card'><table><tr><th>field</th><th>value</th><th>confidence</th></tr>"
    )
    for k, v in result["proposal"].items():
        parts.append(
            f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(v['value']))}</td><td>{v['confidence']}</td></tr>"
        )
    for k in result["missing"]:
        parts.append(
            f"<tr><td>{html.escape(k)}</td><td colspan='2'><span class='chip bad'>missing</span></td></tr>"
        )
    parts.append(
        f"</table><div class='muted'>clicks per state: {html.escape(str(result['zones'].get('clicks_by_state', {})))}</div></div>"
    )

    # leaderboard
    parts.append(
        "<h2>Learner strategies</h2><div class='card'><table><tr><th>strategy</th><th>agree</th><th>known</th><th>rounds</th><th>note</th></tr>"
    )
    for row in result["leaderboard"]:
        parts.append(
            f"<tr><td>{html.escape(row['strategy'])}</td><td>{row['agree']}</td><td>{row['known']}</td><td>{row['total']}</td><td class='muted'>{html.escape(row.get('note', ''))}</td></tr>"
        )
    parts.append("</table></div>")

    # answers
    parts.append(
        "<h2>Seeded answers</h2><div class='card'><table><tr><th>fact</th><th>answer</th><th>confidence</th></tr>"
    )
    for a in result["answers"]:
        parts.append(
            f"<tr><td>{html.escape(a['display'])}</td><td>{a['answer']}</td><td>{a['confidence']}</td></tr>"
        )
    if not result["answers"]:
        parts.append(
            "<tr><td colspan='3' class='muted'>none (needs F7/F8 markers and a fact region)</td></tr>"
        )
    parts.append("</table></div></body></html>")
    out = session_dir / "report.html"
    out.write_text("".join(parts), encoding="utf-8")
    return out
