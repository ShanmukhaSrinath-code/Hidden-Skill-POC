"""Redraw docs/images/skill-graph.svg from the live skill graph (run from the project folder: python docs/make_skill_graph.py)."""
import math
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

sys.path.insert(0, ".")
load_dotenv()
INK, MUTED, BLUE, TEAL, AMBER, LINE, GREY = "#14213d", "#5b6b85", "#2f6fed", "#0f9d8f", "#b7791f", "#dfe6f0", "#c3cddd"


def esc(t):
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn, conn.cursor() as cur:
    cur.execute("""SELECT e.emp_id, e.full_name, e.role, count(*) FILTER (WHERE es.kind = 'discovered') AS disc,
                          sum(es.evidence_count) AS ev
                   FROM employees e JOIN employee_skills es ON es.emp_id = e.emp_id
                   GROUP BY 1, 2, 3 ORDER BY disc DESC, ev DESC LIMIT 1""")
    who = cur.fetchone()
    cur.execute("""SELECT s.name, es.kind, es.evidence_count, es.explicit_count, es.confidence, es.requester_emp_ids
                   FROM employee_skills es JOIN skills s ON s.skill_id = es.skill_id
                   WHERE es.emp_id = %s ORDER BY es.kind, es.evidence_count DESC, s.name""", (who["emp_id"],))
    skills = cur.fetchall()
    cur.execute("SELECT emp_id, full_name FROM employees")
    names = {r["emp_id"]: r["full_name"] for r in cur.fetchall()}
    cur.execute("""SELECT s.name AS skill, ev.evidence_id, ev.validation, ev.score, ev.signal, ev.source, ev.requester_emp_id
                   FROM skill_evidence ev JOIN skills s ON s.skill_id = ev.skill_id
                   WHERE ev.emp_id = %s ORDER BY (ev.validation = 'explicit') DESC, ev.score DESC LIMIT 1""", (who["emp_id"],))
    sample = cur.fetchone()

explicit = [s for s in skills if s["kind"] == "explicit"][:6]
discovered = [s for s in skills if s["kind"] == "discovered"][:6]
W, H, cx, cy = 1200, 470, 600, 215
out = []
out.append(f'<text x="40" y="44" font-size="13" font-weight="800" fill="{MUTED}" letter-spacing="1.4">SKILL GRAPH · {esc(who["full_name"]).upper()} · LIVE DATA</text>')
out.append(f'<text x="250" y="80" font-size="13" font-weight="800" fill="{BLUE}" text-anchor="middle" letter-spacing="1">DECLARED (HRMS / RESUME)</text>')
out.append(f'<text x="950" y="80" font-size="13" font-weight="800" fill="{AMBER}" text-anchor="middle" letter-spacing="1">DISCOVERED FROM WORK</text>')


def node(s, x, y, side):
    has = s["evidence_count"] > 0
    color = AMBER if s["kind"] == "discovered" else (BLUE if has else GREY)
    edge_x = x + (200 if side == "left" else 0)
    parts = [f'<path d="M{edge_x} {y + 21} C{(edge_x + cx) / 2} {y + 21} {(edge_x + cx) / 2} {cy} {cx + (-70 if side == "left" else 70)} {cy}" '
             f'stroke="{color}" stroke-width="{2.2 if has else 1.2}" fill="none"' + ("" if has else ' stroke-dasharray="5 5"') + "/>"]
    fill = "#fcf0dc" if s["kind"] == "discovered" else ("#eef3fd" if has else "#f6f8fb")
    parts.append(f'<rect x="{x}" y="{y}" width="200" height="42" rx="11" fill="{fill}" stroke="{color}" stroke-width="1.5"/>')
    parts.append(f'<text x="{x + 14}" y="{y + 18}" font-size="13" font-weight="700" fill="{INK}">{esc(s["name"][:24])}</text>')
    meta = (f'{s["evidence_count"]} evidence · {s["explicit_count"]} confirmed · {float(s["confidence"]):.2f}'
            if has else "declared · no evidence yet")
    parts.append(f'<text x="{x + 14}" y="{y + 34}" font-size="11" fill="{MUTED}">{esc(meta)}</text>')
    return "".join(parts)


for i, s in enumerate(explicit):
    out.append(node(s, 150, 96 + i * 52, "left"))
for i, s in enumerate(discovered):
    out.append(node(s, 850, 96 + i * 52, "right"))
if not discovered:
    out.append(f'<text x="950" y="130" font-size="13" fill="{MUTED}" text-anchor="middle">none yet</text>')
out.append(f'<circle cx="{cx}" cy="{cy}" r="70" fill="{INK}"/>'
           f'<text x="{cx}" y="{cy - 6}" font-size="20" font-weight="800" fill="#fff" text-anchor="middle">{esc(who["full_name"])}</text>'
           f'<text x="{cx}" y="{cy + 16}" font-size="12" fill="#9fb8e6" text-anchor="middle">{esc(who["emp_id"])}</text>'
           f'<text x="{cx}" y="{cy + 34}" font-size="11" fill="#ffd99a" text-anchor="middle">{esc(who["role"])}</text>')
if sample:
    asked = names.get(sample["requester_emp_id"], "a colleague")
    tag = "CONFIRMED" if sample["validation"] == "explicit" else f"JUDGED {sample['score']}/10"
    out.append(f'<rect x="40" y="{H - 104}" width="{W - 80}" height="78" rx="14" fill="#f6f8fb" stroke="{LINE}"/>'
               f'<text x="60" y="{H - 76}" font-size="12" font-weight="800" fill="{MUTED}" letter-spacing="1">EVIDENCE BEHIND A SKILL</text>'
               f'<rect x="60" y="{H - 62}" width="{len(tag) * 8 + 18}" height="22" rx="6" fill="#e2f5f1"/>'
               f'<text x="{60 + (len(tag) * 8 + 18) / 2}" y="{H - 46}" font-size="11.5" font-weight="800" fill="{TEAL}" text-anchor="middle">{tag}</text>'
               f'<text x="{90 + len(tag) * 8}" y="{H - 46}" font-size="13" fill="{INK}"><tspan font-weight="700">{esc(sample["skill"])}</tspan>'
               f' · helped {esc(asked)} · {esc(sample["source"])} · {esc((sample["signal"] or "")[:70])}</text>'
               f'<text x="{W - 60}" y="{H - 46}" font-size="11" fill="#9aa6ba" text-anchor="end" font-family="Consolas, monospace">{esc(sample["evidence_id"])} → message link</text>')
body = "".join(out)
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
       f'aria-label="Skill graph of {esc(who["full_name"])}" font-family="Segoe UI, Inter, Helvetica, Arial, sans-serif">'
       f'<title>Skill graph of {esc(who["full_name"])}: declared vs discovered skills</title>'
       f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="20" fill="#fff" stroke="{LINE}" stroke-width="2"/>{body}</svg>\n')
Path("docs/images/skill-graph.svg").write_text(svg, encoding="utf-8")
print("skill-graph.svg for", who["full_name"], "| declared:", len(explicit), "| discovered:", [s["name"] for s in discovered])
