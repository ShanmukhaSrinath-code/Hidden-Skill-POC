"""Local live dashboard for Slack (ingest.py), Jira and ClickUp (integrations.sync) message mappings.

Run with: .venv\\Scripts\\python.exe dashboard.py

The dashboard itself only listens on 127.0.0.1. Jira/ClickUp webhooks are received by a separate
listener (WEBHOOK_PORT, default 8001) that only accepts POST /webhooks/<platform>/<WEBHOOK_TOKEN>,
so a tunnel such as ngrok can expose webhooks without exposing the dashboard's message data.
"""
import hashlib
import hmac
import json
import os
import queue
from pathlib import Path
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

load_dotenv()

SYNC_STATE = {"status": "starting", "last_sync": None, "next_sync": None, "error": None}
SYNC_LOCK = threading.Lock()
PLATFORM_STATE = {name: {"status": "not configured", "last_sync": None, "error": None}
                  for name in ("jira", "confluence", "clickup", "identity", "skills")}
WEBHOOK_QUEUE = queue.Queue()

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Slack · Message mapping</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#111a2d;--line:#23314b;--muted:#91a0b8;--text:#edf2fb;--cyan:#56d5e8;--green:#60dfa2;--amber:#ffc66d}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 80% -15%,#183052 0,transparent 38%),var(--bg);color:var(--text);font:14px/1.5 Inter,ui-sans-serif,system-ui,Segoe UI,sans-serif}
main{max-width:1440px;margin:auto;padding:34px 32px 56px}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:30px}.brand{display:flex;gap:12px;align-items:center}.logo{width:38px;height:38px;border-radius:12px;background:linear-gradient(135deg,#40c7dc,#4c78f0);display:grid;place-items:center;font-weight:800;color:#07121e;font-size:19px}.eyebrow{color:var(--cyan);font-size:11px;letter-spacing:.15em;font-weight:700;text-transform:uppercase}.title{font-size:24px;font-weight:700;letter-spacing:-.03em;margin-top:2px}.live{display:flex;align-items:center;gap:9px;color:var(--muted);font-size:12px}.dot{width:8px;height:8px;background:var(--green);border-radius:50%;box-shadow:0 0 12px #60dfa277}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}.card,.panel{background:linear-gradient(145deg,#131e33,#101829);border:1px solid var(--line);border-radius:14px}.card{padding:18px 20px}.label{color:var(--muted);font-size:12px}.value{font-size:27px;font-weight:700;margin-top:7px;letter-spacing:-.04em}.sub{font-size:11px;color:#71819c;margin-top:2px}.layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px}.panel{overflow:hidden}.panel-head{display:flex;justify-content:space-between;align-items:center;padding:18px 20px;border-bottom:1px solid var(--line)}h2{font-size:14px;margin:0}.hint{font-size:11px;color:var(--muted)}.stream{max-height:calc(100vh - 330px);min-height:300px;overflow:auto}.row{padding:16px 20px;border-bottom:1px solid #202d42;display:grid;grid-template-columns:1fr auto;gap:12px}.row:last-child{border:0}.message{font-size:13px;line-height:1.6;color:#dce5f3;word-break:break-word}.meta{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-bottom:8px;color:var(--muted);font-size:11px}.who{color:#fff;font-weight:650}.pill{border:1px solid #2a3a55;background:#17243a;border-radius:999px;padding:2px 8px;font-size:10px;color:#b8c8dc}.pill.cyan{color:var(--cyan);border-color:#265867;background:#102b39}.pill.green{color:var(--green);border-color:#245741;background:#10291f}.mapping{margin-top:11px;display:flex;flex-wrap:wrap;gap:6px}.map-item{font-size:10px;padding:3px 8px;border-radius:6px;background:#101c30;border:1px solid #263650;color:#adbed5}.map-item b{color:#768ba9;font-weight:500;margin-right:4px}.reaction{color:var(--amber)}.when{white-space:nowrap;color:#8190a8;font-size:10px}.side{padding-bottom:10px}.section{padding:16px 18px;border-bottom:1px solid var(--line)}.section:last-child{border:0}.section h3{font-size:11px;color:#94a4bd;text-transform:uppercase;letter-spacing:.1em;margin:0 0 13px}.status{display:grid;grid-template-columns:1fr auto;gap:8px;font-size:12px;padding:5px 0;color:#c4d0e0}.status span:last-child{color:var(--green);font-variant-numeric:tabular-nums}.bar{height:5px;background:#25334b;border-radius:5px;overflow:hidden;margin:7px 0 10px}.bar i{display:block;background:linear-gradient(90deg,#48cde0,#4a89fb);height:100%;border-radius:5px}.checkpoint{padding:8px 0;border-bottom:1px solid #202d42;font-size:11px}.checkpoint:last-child{border:0}.cktop{display:flex;justify-content:space-between;gap:8px}.ckname{color:#e0e9f5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cktime{color:#8090a8;white-space:nowrap}.ckdetail{color:#8190a8;margin-top:3px}.empty{padding:50px 20px;text-align:center;color:var(--muted)}.footer{color:#657590;font-size:10px;margin-top:14px;display:flex;justify-content:space-between}.error{color:#ff9999}.filterbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line);background:#0f1828}.filterbar label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.filterbar select{background:#17243a;color:#dce5f3;border:1px solid #2a3a55;border-radius:8px;padding:7px 10px;font:inherit;font-size:12px;min-width:170px}.filterbar button{background:none;border:1px solid #2a3a55;color:var(--muted);border-radius:8px;padding:6px 10px;font:inherit;font-size:12px;cursor:pointer}.pill.amber{color:var(--amber);border-color:#6b5426;background:#2b2210}a.src{color:var(--cyan);font-size:11px;text-decoration:none}a.src:hover{text-decoration:underline}@media(max-width:950px){.layout{grid-template-columns:1fr}.stream{max-height:60vh}}@media(max-width:600px){main{padding:22px 14px}.grid{grid-template-columns:repeat(2,1fr);gap:9px}.card{padding:13px}.value{font-size:22px}.top{margin-bottom:20px}.title{font-size:19px}.row{padding:14px}.when{grid-column:1/-1}}
</style></head><body><main>
<header class="top"><div class="brand"><div class="logo">↗</div><div><div class="eyebrow">Agent messaging intelligence</div><div class="title">Message mapping</div></div></div><div style="display:flex;gap:18px;align-items:center"><a href="/skills" style="color:#ffc66d;font-size:13px;text-decoration:none;border:1px solid #6b5426;border-radius:999px;padding:5px 12px">Skill graph ↗</a><a href="/how-it-works" style="color:var(--cyan);font-size:13px;text-decoration:none;border:1px solid #265867;border-radius:999px;padding:5px 12px">How it works ↗</a><div class="live"><i class="dot"></i><span id="liveText">Connecting to PostgreSQL</span></div></div></header>
<section class="grid"><div class="card"><div class="label">Messages mapped</div><div class="value" id="messages">—</div><div class="sub">Stored in PostgreSQL</div></div><div class="card"><div class="label">Conversations</div><div class="value" id="conversations">—</div><div class="sub">Channels, DMs, Jira issues, ClickUp tasks and chats</div></div><div class="card"><div class="label">People linked</div><div class="value" id="people">—</div><div class="sub" id="peopleSub">Slack, Jira, Confluence and ClickUp people</div></div><div class="card"><div class="label">Mentions mapped</div><div class="value" id="mentions">—</div><div class="sub">Users, groups, channels and broadcasts</div></div></section>
<section class="layout"><div class="panel"><div class="panel-head"><div><h2>Live message stream</h2><div class="hint">Slack, Jira, Confluence and ClickUp sync automatically · this view refreshes every 2 seconds</div></div><span class="pill cyan" id="updated">Waiting</span></div><div class="filterbar"><label for="fPerson">Person</label><select id="fPerson"><option value="">All people</option></select><label for="fPlatform">Platform</label><select id="fPlatform"><option value="">All platforms</option><option value="slack">Slack</option><option value="jira">Jira</option><option value="confluence">Confluence</option><option value="clickup">ClickUp</option></select><button id="fClear" type="button">Clear filters</button></div><div class="stream" id="stream"><div class="empty">Waiting for PostgreSQL data…<br>Start the dashboard to sync Slack automatically.</div></div></div>
<aside class="panel side"><div class="section"><h3>Mapping coverage</h3><div class="status"><span>Sender → message</span><span>LINKED</span></div><div class="status"><span>Conversation → message</span><span>LINKED</span></div><div class="status"><span>Participants → conversation</span><span>LINKED</span></div><div class="status"><span>Mentions → target</span><span>LINKED</span></div><div class="status"><span>Thread / reactions</span><span>LINKED</span></div></div><div class="section"><h3>Ingestion checkpoints</h3><div id="checkpoints" class="hint">No sync activity yet.</div></div><div class="section"><h3>Identity resolution</h3><div id="identity" class="hint">Runs after the first sync.</div></div></aside></section><div class="footer"><span>Local dashboard · reads mapped records from PostgreSQL</span><span id="footerStatus"></span></div>
</main><script>
const F={platform:'',email:''};
function fillPeople(list){const s=document.getElementById('fPerson'),key=JSON.stringify(list||[]);if(s.dataset.k===key)return;s.dataset.k=key;s.innerHTML='<option value="">All people</option>'+(list||[]).map(p=>`<option value="${esc(p.email)}">${p.emp_id?esc(p.emp_id)+' · ':''}${esc(p.name)}${p.role?' · '+esc(p.role):''} (${esc(p.platforms.join(', ')||'not linked yet')})</option>`).join('');s.value=F.email}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function date(v){if(!v)return '—';const d=new Date(v);return d.toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'})}
function render(d){document.getElementById('messages').textContent=Number(d.stats.messages||0).toLocaleString();document.getElementById('conversations').textContent=Number(d.stats.conversations||0).toLocaleString();document.getElementById('people').textContent=Number(d.stats.people||0).toLocaleString();document.getElementById('mentions').textContent=Number(d.stats.mentions||0).toLocaleString();
const sync=d.sync||{};document.getElementById('liveText').textContent=sync.status==='syncing'?'Slack sync running · connected':'Slack sync '+(sync.status||'starting');document.getElementById('footerStatus').textContent=sync.error?'Sync error: '+sync.error:(sync.last_sync?'Last Slack sync '+date(sync.last_sync):'Waiting for first Slack sync');Object.entries(d.platforms||{}).forEach(([n,p])=>{document.getElementById('footerStatus').textContent+=' · '+n+': '+(p.error?'error: '+p.error:(p.status||'not configured'))+(p.last_sync?' at '+date(p.last_sync):'')});const fl=d.filters||{};fillPeople(fl.people);const idn=d.identity;document.getElementById('identity').innerHTML=idn?`<div class="status"><span>Employees (EMP_ID)</span><span>${idn.employees}</span></div>${idn.accounts.map(a=>`<div class="status"><span>${esc(a.platform)} accounts linked</span><span>${a.matched}/${a.total}</span></div>`).join('')}<div class="status"><span>Canonical activity records</span><span>${idn.records}</span></div><div class="status"><span>Threads (Agent 1 input)</span><span>${idn.threads}</span></div>`:'Runs after the first sync.';document.getElementById('peopleSub').textContent=fl.email?'Accounts linked: '+((fl.linked_platforms||[]).join(', ')||'none'):'Slack, Jira, Confluence and ClickUp people';document.getElementById('updated').textContent=`${d.messages.length} recent`+(fl.email||fl.platform?' · filtered':'');
document.getElementById('stream').innerHTML=d.messages.length?d.messages.map(m=>`<article class="row"><div><div class="meta"><span class="who">${esc(m.sender||m.sender_id||'Unknown sender')}</span><span>in</span><span class="pill cyan">${esc(m.conversation||m.conversation_id)}</span><span class="pill">${esc(m.type||'conversation')}</span>${m.is_thread_reply?'<span class="pill green">thread reply</span>':''}${m.role?`<span class="pill amber">${esc(m.role.toUpperCase())}</span>`:''}${m.link?`<a class="src" href="${esc(m.link)}" target="_blank" rel="noopener">open ↗</a>`:''}</div><div class="message">${esc(m.text||'(no text)')}</div>${(m.details||[]).length?`<div class="mapping">${m.details.map(x=>{const i=x.indexOf(' ');return `<span class="map-item"><b>${esc(x.slice(0,i))}</b>${esc(x.slice(i+1))}</span>`}).join('')}</div>`:''}<div class="mapping"><span class="map-item"><b>SENDER</b>${esc(m.sender||m.sender_id||'unknown')}</span><span class="map-item"><b>RECIPIENTS</b>${esc((m.recipients||[]).join(', ')||'conversation members')}</span>${(m.mentions||[]).map(x=>`<span class="map-item"><b>${esc(x.type.toUpperCase())}</b>${esc(x.target)}</span>`).join('')}${(m.reactions||[]).map(x=>`<span class="map-item reaction">:${esc(x.name)}: ${esc(x.count)}</span>`).join('')}</div></div><div class="when">${date(m.sent_at)}</div></article>`).join(''):'<div class="empty">No messages match.<br>Change the filters, or start ingestion to see mappings appear.</div>';
document.getElementById('checkpoints').innerHTML=d.checkpoints.length?d.checkpoints.map(c=>`<div class="checkpoint"><div class="cktop"><span class="ckname">${esc(c.name||c.conversation_id)}</span><span class="${c.status==='ok'?'pill green':'pill'}">${esc(c.status||'pending')}</span></div><div class="ckdetail">${esc(c.collected_via||'—')} · ${date(c.last_run)}${c.detail?' · '+esc(c.detail):''}</div></div>`).join(''):'No sync activity yet.'}
async function poll(){try{const r=await fetch('/api/data?'+new URLSearchParams(F),{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||'Database unavailable');render(d)}catch(e){document.getElementById('liveText').textContent='Database unavailable';document.getElementById('footerStatus').innerHTML=`<span class="error">${esc(e.message)}</span>`}}document.getElementById('fPerson').onchange=e=>{F.email=e.target.value;poll()};document.getElementById('fPlatform').onchange=e=>{F.platform=e.target.value;poll()};document.getElementById('fClear').onclick=()=>{F.email='';F.platform='';document.getElementById('fPerson').value='';document.getElementById('fPlatform').value='';poll()};poll();setInterval(poll,2000);
</script></body></html>'''


PLATFORMS = ("slack", "jira", "confluence", "clickup")

UNIFIED_COLUMNS = """mm.conversation_id, mm.object_id AS message_ts, mm.sender_id, mm.sender, mm.platform,
                    CASE WHEN mm.platform = 'jira' AND mm.conversation_type = 'issue' THEN mm.conversation_id || ' · ' || COALESCE(mm.conversation_title, '')
                         ELSE COALESCE(mm.conversation_title, mm.conversation_id) END AS conversation,
                    mm.platform || ' ' || CASE mm.object_type
                        WHEN 'chat_message' THEN lower(replace(COALESCE(t.attributes->>'channel_type', 'chat'), '_', ' '))
                        WHEN 'issue' THEN 'issue' WHEN 'task' THEN 'task' WHEN 'comment' THEN 'comment'
                        WHEN 'page' THEN 'page' WHEN 'blogpost' THEN 'blog post' WHEN 'inline_comment' THEN 'inline comment'
                        WHEN 'edit' THEN 'edit' WHEN 'task_item' THEN 'task item'
                        ELSE 'change' END AS type,
                    CASE WHEN mm.kind IN ('task', 'document') THEN concat_ws(' — ', mm.title, NULLIF(mm.body, '')) ELSE mm.body END AS text,
                    mm.occurred_at AS sent_at,
                    (mm.kind = 'comment' OR t.attributes->>'reply_to' IS NOT NULL) AS is_thread_reply, mm.recipients,
                    (SELECT json_agg(d) FROM unnest(ARRAY[
                        'STATUS ' || (t.attributes->>'status'), 'TYPE ' || (t.attributes->>'issue_type'),
                        'ASSIGNEE ' || COALESCE((SELECT string_agg(DISTINCT COALESCE(u.title, p.user_id), ', ')
                            FROM unified_participants p LEFT JOIN unified_records u
                              ON u.platform = p.platform AND u.account_id = p.account_id
                             AND u.object_type = 'user' AND u.object_id = p.user_id
                            WHERE p.platform = t.platform AND p.account_id = t.account_id AND p.object_type = t.object_type
                              AND p.object_id = t.object_id AND p.role = 'assignee'), 'unassigned'),
                        'PRIORITY ' || (t.attributes->>'priority'), 'DUE ' || (t.attributes->>'due_date'),
                        'LIST ' || (t.attributes->>'list_name'), 'SPACE ' || (t.attributes->>'space_name'),
                        'VERSION ' || (t.attributes->>'version'), 'PARENT ' || (t.attributes->>'parent_key'),
                        'LABELS ' || NULLIF(array_to_string(ARRAY(SELECT jsonb_array_elements_text(t.attributes->'labels')), ', '), '')
                    ]) d WHERE d IS NOT NULL AND mm.kind IN ('task', 'document')) AS details,
                    (SELECT COALESCE(json_agg(json_build_object('type', 'user', 'target', x)), '[]'::json)
                       FROM json_array_elements_text(mm.mentions) x) AS mentions,
                    (SELECT COALESCE(json_agg(json_build_object('name', rx.reaction, 'count', rx.n) ORDER BY rx.reaction), '[]'::json)
                       FROM (SELECT reaction, count(*) AS n FROM unified_reactions r
                             WHERE r.platform = mm.platform AND r.account_id = mm.account_id
                               AND r.object_type = mm.object_type AND r.object_id = mm.object_id
                             GROUP BY reaction) rx) AS reactions"""

# How the selected person relates to a Jira/Confluence/ClickUp item: sent, received and/or mentioned.
UNIFIED_ROLE = """(SELECT string_agg(DISTINCT pm.direction, ', ') FROM unified_person_messages pm
                     WHERE pm.platform = mm.platform AND pm.object_type = mm.object_type AND pm.object_id = mm.object_id
                       AND pm.platform || ':' || pm.person_id = ANY(%(keys)s))"""


def identities(cur):
    """Every human account on every platform, as (platform, user id, email, name[, emp_id, role]).

    Uses Phase 1 identity resolution (person_accounts -> employees, incl. manual overrides) when it has run;
    otherwise falls back to matching the email stored on each platform account.
    """
    cur.execute("SELECT to_regclass('person_accounts') IS NOT NULL AND EXISTS (SELECT 1 FROM pg_tables "
                "WHERE tablename = 'employees') AS ready")
    if cur.fetchone()["ready"]:
        cur.execute("""SELECT pa.platform, pa.platform_user_id AS user_id, e.email, e.full_name AS name,
                              e.emp_id, e.role
                       FROM person_accounts pa JOIN employees e ON e.emp_id = pa.emp_id""")
        rows = cur.fetchall()
        if rows:
            return rows
    cur.execute("""SELECT 'slack' AS platform, user_id, lower(email) AS email,
            COALESCE(NULLIF(display_name,''), NULLIF(real_name,''), username) AS name
        FROM slack_users WHERE COALESCE(email,'') <> '' AND NOT is_bot AND NOT is_deleted""")
    rows = cur.fetchall()
    cur.execute("SELECT to_regclass('unified_records') IS NOT NULL AS ready")
    if cur.fetchone()["ready"]:
        cur.execute("""SELECT platform, object_id AS user_id, lower(attributes->>'email') AS email, title AS name
            FROM unified_records WHERE kind = 'person' AND COALESCE(attributes->>'email','') <> ''
              AND NOT COALESCE((attributes->>'is_bot')::boolean, false)""")
        rows += cur.fetchall()
    return rows


def fetch_data(platform=None, email=None):
    """Dashboard data, optionally narrowed to one platform and/or one person (by email).

    A person filter keeps every item that person sent, received (member/participant) or was mentioned in,
    on every platform where their email is linked to an account.
    """
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is missing from .env")
    platform = platform if platform in PLATFORMS else None
    email = (email or "").strip().lower() or None
    with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            people_rows = identities(cur)
            linked = [r for r in people_rows if email and r["email"] == email]
            slack_ids = [r["user_id"] for r in linked if r["platform"] == "slack"]
            keys = [f"{r['platform']}:{r['user_id']}" for r in linked if r["platform"] != "slack"]
            stats = {"messages": 0, "conversations": 0, "mentions": 0,
                     "people": len(linked) if email else len({(r["platform"], r["user_id"]) for r in people_rows})}
            messages = []

            if platform in (None, "slack"):
                where, role = "", "NULL"
                if email:
                    member = """EXISTS (SELECT 1 FROM conversation_members cm WHERE cm.conversation_id = m.conversation_id
                                AND cm.user_id = ANY(%(ids)s))"""
                    mentioned = """EXISTS (SELECT 1 FROM slack_mentions sm WHERE sm.conversation_id = m.conversation_id
                                   AND sm.message_ts = m.message_ts AND sm.target_id = ANY(%(ids)s))"""
                    where = f"WHERE (m.user_id = ANY(%(ids)s) OR {member} OR {mentioned})"
                    role = f"""concat_ws(', ', CASE WHEN m.user_id = ANY(%(ids)s) THEN 'sent' END,
                               CASE WHEN m.user_id <> ALL(%(ids)s) AND {member} THEN 'received' END,
                               CASE WHEN {mentioned} THEN 'mentioned' END)"""
                params = {"ids": slack_ids}
                cur.execute(f"""SELECT count(*) AS messages, count(DISTINCT m.conversation_id) AS conversations,
                    (SELECT count(*) FROM slack_mentions x WHERE (x.conversation_id, x.message_ts) IN
                        (SELECT m.conversation_id, m.message_ts FROM slack_messages m {where})) AS mentions
                    FROM slack_messages m {where}""", params)
                for key, value in cur.fetchone().items():
                    stats[key] += value
                cur.execute(f"""SELECT m.conversation_id,m.message_ts,m.user_id AS sender_id,'slack' AS platform,
                    COALESCE(NULLIF(u.display_name,''),NULLIF(u.real_name,''),u.username,m.user_id) AS sender,
                    COALESCE(c.name,CASE c.conversation_type WHEN 'im' THEN 'Direct message' WHEN 'mpim' THEN 'Group message' ELSE c.conversation_type END,c.conversation_id) AS conversation,
                    'slack ' || CASE c.conversation_type WHEN 'im' THEN 'dm' WHEN 'mpim' THEN 'group dm'
                        WHEN 'public_channel' THEN 'channel' WHEN 'private_channel' THEN 'private channel'
                        ELSE COALESCE(c.conversation_type, 'message') END AS type,
                    m.message_text AS text,m.sent_at,m.is_thread_reply,m.permalink AS link,{role} AS role,
                    COALESCE((SELECT json_agg(COALESCE(NULLIF(ru.display_name,''),NULLIF(ru.real_name,''),ru.username,cm.user_id) ORDER BY cm.user_id)
                        FROM conversation_members cm LEFT JOIN slack_users ru ON ru.user_id=cm.user_id
                        WHERE cm.conversation_id=m.conversation_id AND cm.user_id<>COALESCE(m.user_id,'')), '[]'::json) AS recipients,
                    COALESCE((SELECT json_agg(json_build_object('type',sm.mention_type,'target',COALESCE(NULLIF(tu.display_name,''),NULLIF(tu.real_name,''),tu.username,sm.target_id)) ORDER BY sm.mention_type,sm.target_id)
                        FROM slack_mentions sm LEFT JOIN slack_users tu ON tu.user_id=sm.target_id
                        WHERE sm.conversation_id=m.conversation_id AND sm.message_ts=m.message_ts), '[]'::json) AS mentions,
                    COALESCE((SELECT json_agg(json_build_object('name',rx.reaction,'count',rx.n) ORDER BY rx.reaction)
                        FROM (SELECT reaction,count(*) AS n FROM slack_reactions
                              WHERE conversation_id=m.conversation_id AND message_ts=m.message_ts GROUP BY reaction) rx), '[]'::json) AS reactions
                    FROM slack_messages m LEFT JOIN slack_users u ON u.user_id=m.user_id
                    LEFT JOIN slack_conversations c ON c.conversation_id=m.conversation_id
                    {where} ORDER BY m.sent_at DESC LIMIT 60""", params)
                messages = cur.fetchall()
                # Show Slack's <@U123> / <#C123|name> / <!here> markup as readable @name / #name / @here.
                cur.execute("""SELECT user_id, COALESCE(NULLIF(display_name,''), NULLIF(real_name,''), username) AS name
                    FROM slack_users""")
                names = {r["user_id"]: r["name"] for r in cur.fetchall()}
                for m in messages:
                    m["text"] = re.sub(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>", lambda x: "@" + str(names.get(x.group(1), x.group(1))),
                                       m["text"] or "")
                    m["text"] = re.sub(r"<#[A-Z0-9]+\|([^>]*)>", r"#", m["text"])
                    m["text"] = re.sub(r"<!(here|channel|everyone)[^>]*>", r"@", m["text"])

            cur.execute("""SELECT cp.conversation_id,cp.collected_via,cp.status,cp.detail,cp.last_run,
                COALESCE(c.name,CASE c.conversation_type WHEN 'im' THEN 'Direct message' WHEN 'mpim' THEN 'Group message' ELSE c.conversation_type END,cp.conversation_id) AS name
                FROM ingestion_checkpoints cp LEFT JOIN slack_conversations c USING(conversation_id)
                ORDER BY cp.last_run DESC NULLS LAST LIMIT 12""")
            checkpoints = cur.fetchall()

            cur.execute("SELECT to_regclass('unified_reactions') IS NOT NULL AS ready")
            if cur.fetchone()["ready"] and platform != "slack":
                conditions, params = [], {"keys": keys, "platform": platform}
                if platform:
                    conditions.append("mm.platform = %(platform)s")
                if email:
                    conditions.append(f"{UNIFIED_ROLE} IS NOT NULL")
                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                cur.execute(f"""SELECT count(*) AS messages,
                    count(DISTINCT mm.platform || mm.conversation_type || mm.conversation_id) AS conversations,
                    COALESCE(sum(json_array_length(mm.mentions)), 0) AS mentions
                    FROM unified_message_map mm {where}""", params)
                for key, value in cur.fetchone().items():
                    stats[key] += value
                cur.execute(f"""SELECT {UNIFIED_COLUMNS}, mm.source_url AS link,
                    {UNIFIED_ROLE if email else 'NULL'} AS role
                    FROM unified_message_map mm
                    JOIN unified_records t ON t.platform = mm.platform AND t.account_id = mm.account_id
                     AND t.object_type = mm.object_type AND t.object_id = mm.object_id
                    {where} ORDER BY mm.occurred_at DESC NULLS LAST LIMIT 60""", params)
                messages = sorted(messages + cur.fetchall(), key=lambda m: str(m["sent_at"] or ""), reverse=True)[:60]
                cur.execute("""SELECT scope_id AS conversation_id, account_id AS collected_via, status, detail, last_run,
                    platform || ': ' || scope_id AS name FROM unified_sync_checkpoints WHERE scope_id NOT LIKE 'chat:%'""")
                checkpoints = sorted(checkpoints + cur.fetchall(), key=lambda c: str(c["last_run"] or ""),
                                     reverse=True)[:12]

    # Person filter options: employees (EMP_ID) when identity resolution has run, else FILTER_EMAILS / emails.
    options, identity = [], None
    if people_rows and "emp_id" in people_rows[0]:
        with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("SELECT emp_id, email, full_name, role FROM employees ORDER BY emp_id")
            for e in cur.fetchall():
                options.append({"email": e["email"], "name": e["full_name"], "emp_id": e["emp_id"], "role": e["role"],
                                "platforms": sorted({r["platform"] for r in people_rows if r["emp_id"] == e["emp_id"]},
                                                    key=PLATFORMS.index)})
            cur.execute("""SELECT platform, count(*) FILTER (WHERE emp_id IS NOT NULL) AS matched, count(*) AS total
                           FROM person_accounts GROUP BY 1""")
            accounts = {r["platform"]: r for r in cur.fetchall()}
            cur.execute("SELECT to_regclass('activity_threads') IS NOT NULL AS ready")
            ready = cur.fetchone()["ready"]
            records = threads = 0
            if ready:
                cur.execute("SELECT (SELECT count(*) FROM activity_records) AS records, "
                            "(SELECT count(*) FROM activity_threads) AS threads")
                row = cur.fetchone()
                records, threads = row["records"], row["threads"]
            identity = {"employees": len(options), "records": records, "threads": threads,
                        "accounts": [{"platform": k, "matched": accounts[k]["matched"], "total": accounts[k]["total"]}
                                     for k in PLATFORMS if k in accounts]}
    configured = [e.strip().lower() for e in os.getenv("FILTER_EMAILS", "").split(",") if e.strip()]
    for address in ([] if options else configured or sorted({r["email"] for r in people_rows})):
        rows = [r for r in people_rows if r["email"] == address]
        # Jira/ClickUp display names are usually nicer than Slack handles ("Ramu" vs "ramudhatla").
        order = ("jira", "confluence", "clickup", "slack")
        names = [r["name"] for r in sorted(rows, key=lambda r: order.index(r["platform"])) if r["name"]]
        options.append({"email": address, "name": names[0] if names else address.split("@")[0],
                        "platforms": sorted({r["platform"] for r in rows}, key=PLATFORMS.index)})
    with SYNC_LOCK:
        platforms = {name: dict(state) for name, state in PLATFORM_STATE.items()}
    return {"stats": stats, "messages": messages, "checkpoints": checkpoints,
            "sync": dict(SYNC_STATE), "platforms": platforms,
            "identity": identity,
            "filters": {"platform": platform, "email": email, "people": options,
                        "linked_platforms": sorted({r["platform"] for r in linked}, key=PLATFORMS.index)}}


def fetch_skills():
    """Data for /skills: employee skill graphs with evidence, expert finder, help edges, hidden skills, pipeline."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(os.getenv("DATABASE_URL"), connect_timeout=3, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('employee_skills') IS NOT NULL AS ready")
        if not cur.fetchone()["ready"]:
            raise RuntimeError("Skill graph not built yet. Run: python -m integrations.skills")
        cur.execute("SELECT emp_id, full_name AS name, role, department FROM employees ORDER BY emp_id")
        employees = cur.fetchall()
        names = {e["emp_id"]: e["name"] for e in employees}
        cur.execute("""SELECT es.emp_id, es.skill_id, sk.name AS skill, sk.category, es.kind, es.declared_source,
                              es.evidence_count, es.explicit_count, es.implicit_avg, es.confidence, es.requester_emp_ids,
                              es.last_seen
                       FROM employee_skills es JOIN skills sk ON sk.skill_id = es.skill_id""")
        skills = cur.fetchall()
        cur.execute("""SELECT evidence_id, emp_id, skill_id, requester_emp_id, validation, score, signal, topic, source,
                              source_url AS url, occurred_at FROM skill_evidence ORDER BY occurred_at DESC""")
        evidence = cur.fetchall()
        cur.execute("SELECT * FROM skill_experts ORDER BY skill, rank")
        experts_rows = cur.fetchall()
        cur.execute("SELECT * FROM help_edges ORDER BY times_helped DESC, helper")
        edges = cur.fetchall()
        cur.execute("SELECT status, count(*) AS n, max(processed_at) AS last FROM agent_thread_runs GROUP BY status")
        runs = cur.fetchall()
        cur.execute("SELECT count(*) AS n FROM activity_threads")
        threads_total = cur.fetchone()["n"]
        cur.execute("SELECT models FROM agent_thread_runs WHERE models IS NOT NULL ORDER BY processed_at DESC LIMIT 1")
        latest = cur.fetchone()
    for e in employees:
        e["skills"] = []
        for sk in (x for x in skills if x["emp_id"] == e["emp_id"]):
            sk["evidence"] = [dict(v, requester=names.get(v["requester_emp_id"])) for v in evidence
                              if v["emp_id"] == e["emp_id"] and v["skill_id"] == sk["skill_id"]]
            e["skills"].append(sk)
    experts = {}
    for r in experts_rows:
        experts.setdefault(r["skill_id"], {"skill_id": r["skill_id"], "skill": r["skill"], "experts": []})["experts"].append(
            {"emp_id": r["emp_id"], "name": r["full_name"], "role": r["role"], "kind": r["kind"], "rank": r["rank"],
             "evidence_count": r["evidence_count"], "explicit_count": r["explicit_count"], "confidence": r["confidence"]})
    hidden = [{"emp_id": x["emp_id"], "name": names.get(x["emp_id"]), "skill": x["skill"],
               "role": next((e["role"] for e in employees if e["emp_id"] == x["emp_id"]), ""),
               "evidence_count": x["evidence_count"], "explicit_count": x["explicit_count"], "confidence": x["confidence"],
               "requesters": [names.get(r, r) for r in x["requester_emp_ids"]]}
              for x in sorted(skills, key=lambda x: -(x["confidence"] or 0)) if x["kind"] == "discovered"]
    status = {r["status"]: r["n"] for r in runs}
    return {"employees": employees, "experts": sorted(experts.values(), key=lambda x: x["skill"]), "edges": edges,
            "hidden": hidden,
            "kpis": {"employees": len(employees), "skills_with_evidence": len({x["skill_id"] for x in skills if x["evidence_count"]}),
                     "evidence": len(evidence), "discovered": len(hidden),
                     "threads_done": sum(status.values()), "threads_total": threads_total},
            "pipeline": {"status": status, "last_run": max((r["last"] for r in runs), default=None),
                         "models": (latest or {}).get("models") or {"agent1": "-", "agent2": "-"}}}


def skills_worker(interval):
    """Phase 2: run Agents 1-4 on new/changed threads on a schedule (local models are slow, so keep this long)."""
    from integrations import skills
    while True:
        time.sleep(interval)
        try:
            stats = skills.run(log=lambda line: print(line, flush=True))
            with SYNC_LOCK:
                PLATFORM_STATE["skills"].update(status=f"ok · {stats['threads']} threads · {stats['evidence']} evidence",
                                                last_sync=time.time() * 1000, error=None)
        except Exception as exc:
            message = f"{exc.__class__.__name__}: {exc}"
            with SYNC_LOCK:
                PLATFORM_STATE["skills"].update(status="error", error=message)
            print(f"Skill pipeline failed: {message}", flush=True)


def slack_sync_worker():
    """Keep Slack flowing into Postgres while the dashboard is running."""
    interval = max(30, int(os.getenv("SLACK_SYNC_INTERVAL_SECONDS", "60")))
    while True:
        cycle_started = time.monotonic()
        try:
            with SYNC_LOCK:
                SYNC_STATE.update(status="syncing", error=None)
            import ingest
            readers = ingest.load_readers()
            ingest.ingest(readers)
            with SYNC_LOCK:
                SYNC_STATE.update(status="waiting", last_sync=time.time() * 1000,
                                   next_sync=(time.time() + interval) * 1000, error=None)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                message = str(exc) or exc.__class__.__name__
            else:
                message = f"{exc.__class__.__name__}: {exc}"
            with SYNC_LOCK:
                SYNC_STATE.update(status="error", error=message,
                                   next_sync=time.time() + interval)
            print(f"Automatic Slack sync failed: {message}", flush=True)
        time.sleep(max(0, interval - (time.monotonic() - cycle_started)))


def platform_sync_worker(name, sync, interval):
    """Poll one platform on an interval; webhooks (when configured) deliver changes in between."""
    cycle = 0
    while True:
        cycle_started = time.monotonic()
        try:
            with SYNC_LOCK:
                PLATFORM_STATE[name].update(status="syncing", error=None)
            count = sync(cycle)
            with SYNC_LOCK:
                PLATFORM_STATE[name].update(status=f"ok · {count} records", last_sync=time.time() * 1000, error=None)
        except Exception as exc:
            message = f"{exc.__class__.__name__}: {exc}"
            with SYNC_LOCK:
                PLATFORM_STATE[name].update(status="error", error=message)
            print(f"Automatic {name} sync failed: {message}", flush=True)
        cycle += 1
        time.sleep(max(0, interval - (time.monotonic() - cycle_started)))


def canonical_worker(interval):
    """Phase 1: re-resolve identities and rebuild canonical activity records from whatever has been synced."""
    from integrations import identity
    while True:
        started = time.monotonic()
        try:
            summary = identity.run()
            records = sum(v["records"] for v in summary["activity"].values())
            with SYNC_LOCK:
                PLATFORM_STATE["identity"].update(status=f"ok · {summary['employees']} employees · {records} records",
                                                  last_sync=time.time() * 1000, error=None)
        except Exception as exc:
            message = f"{exc.__class__.__name__}: {exc}"
            with SYNC_LOCK:
                PLATFORM_STATE["identity"].update(status="error", error=message)
            print(f"Identity / canonical refresh failed: {message}", flush=True)
        time.sleep(max(0, interval - (time.monotonic() - started)))


def webhook_worker():
    """Apply queued Jira/ClickUp webhook events one at a time, so writes never race each other."""
    from integrations import sync
    handlers = {"jira": sync.apply_jira_webhook, "clickup": sync.apply_clickup_webhook}
    while True:
        platform, payload = WEBHOOK_QUEUE.get()
        try:
            detail = handlers[platform](payload)
            with SYNC_LOCK:
                PLATFORM_STATE[platform].update(status=f"webhook · {detail}", last_sync=time.time() * 1000, error=None)
            print(f"{platform} webhook applied: {detail}", flush=True)
        except Exception as exc:
            message = f"{exc.__class__.__name__}: {exc}"
            with SYNC_LOCK:
                PLATFORM_STATE[platform].update(status="webhook error", error=message)
            print(f"{platform} webhook failed: {message}", flush=True)


class WebhookHandler(BaseHTTPRequestHandler):
    """Only POST /webhooks/<jira|clickup>/<WEBHOOK_TOKEN>; signatures are checked when secrets are set."""
    SIGNATURES = {"jira": ("JIRA_WEBHOOK_SECRET", "X-Hub-Signature", "sha256="),
                  "clickup": ("CLICKUP_WEBHOOK_SECRET", "X-Signature", "")}

    def do_POST(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        token = os.getenv("WEBHOOK_TOKEN", "")
        if (len(parts) != 3 or parts[0] != "webhooks" or parts[1] not in self.SIGNATURES
                or not token or not hmac.compare_digest(parts[2], token)):
            return self._reply(404)
        platform = parts[1]
        secret_env, header, prefix = self.SIGNATURES[platform]
        secret = os.getenv(secret_env, "")
        if secret:
            expected = prefix + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(self.headers.get(header, ""), expected):
                return self._reply(401)
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return self._reply(400)
        WEBHOOK_QUEUE.put((platform, payload))
        self._reply(200)

    def do_GET(self):
        self._reply(404)

    def _reply(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[webhook {self.log_date_time_string()}] {fmt % args}")


def slack_socket_worker():
    """Backfill once, then persist incoming Events API message/reaction events."""
    app_token = os.getenv("SLACK_APP_TOKEN", "").strip()
    if not app_token:
        with SYNC_LOCK:
            SYNC_STATE.update(status="token missing", error="Add SLACK_APP_TOKEN to .env and restart the dashboard")
        return

    try:
        import ingest
        import psycopg
        from slack_sdk import WebClient
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.request import SocketModeRequest
        from slack_sdk.socket_mode.response import SocketModeResponse

        readers = ingest.load_readers()
        reader_by_id = {"bot" if r.label == "bot" else r.user_id: r for r in readers}
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL is missing from .env")

        socket_client = SocketModeClient(
            app_token=app_token,
            web_client=WebClient(token=os.getenv("SLACK_BOT_TOKEN")),
        )

        def choose_reader(payload, conversation_id):
            for auth in payload.get("authorizations", []):
                key = "bot" if auth.get("is_bot") else auth.get("user_id")
                if key in reader_by_id:
                    return reader_by_id[key]
            # Some Socket Mode envelopes carry a compact authorization. Try the configured
            # readers in turn when Slack doesn't provide a matching user authorization.
            for candidate in readers:
                try:
                    candidate.client.conversations_info(channel=conversation_id)
                    return candidate
                except Exception:
                    continue
            return None

        def ensure_conversation(cur, reader, conversation_id):
            cur.execute("SELECT 1 FROM slack_conversations WHERE conversation_id=%s", (conversation_id,))
            if cur.fetchone():
                return
            response = reader.client.conversations_info(channel=conversation_id)
            channel = response["channel"]
            members = ingest.fetch_members(reader, channel)
            ingest.save_conversation(cur, channel, members)
            return channel

        def process_event(client, request: SocketModeRequest):
            if request.type != "events_api":
                return
            # Acknowledge promptly; database writes happen after Slack receives the ACK.
            client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
            envelope = request.payload or {}
            event = envelope.get("event") or {}
            event_type = event.get("type")
            channel_id = event.get("channel")
            if not channel_id:
                return
            reader = choose_reader(envelope, channel_id)
            if reader is None:
                raise RuntimeError(f"No configured user token can access conversation {channel_id}")

            try:
                with psycopg.connect(dsn) as conn:
                    with conn.cursor() as cur:
                        if event_type == "message":
                            subtype = event.get("subtype")
                            if subtype == "message_deleted":
                                old = event.get("previous_message") or {}
                                if old.get("ts"):
                                    cur.execute("DELETE FROM slack_messages WHERE conversation_id=%s AND message_ts=%s", (channel_id, old["ts"]))
                            else:
                                message = event.get("message") if subtype == "message_changed" else event
                                if not message or not message.get("ts"):
                                    return
                                ensure_conversation(cur, reader, channel_id)
                                ingest.save_message(cur, channel_id, message, reader.label)
                        elif event_type in {"reaction_added", "reaction_removed"}:
                            item = event.get("item") or {}
                            if item.get("type") == "message" and item.get("ts"):
                                if event_type == "reaction_added":
                                    cur.execute("""INSERT INTO slack_reactions (conversation_id,message_ts,reaction,user_id)
                                        VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                                        (channel_id, item["ts"], event.get("reaction"), event.get("user")))
                                else:
                                    cur.execute("""DELETE FROM slack_reactions WHERE conversation_id=%s AND message_ts=%s
                                        AND reaction=%s AND user_id=%s""",
                                        (channel_id, item["ts"], event.get("reaction"), event.get("user")))
                with SYNC_LOCK:
                    SYNC_STATE.update(status="connected · receiving events", last_sync=time.time() * 1000, error=None)
            except Exception as exc:
                message = f"{exc.__class__.__name__}: {exc}"
                with SYNC_LOCK:
                    SYNC_STATE.update(status="event write error", error=message)
                print(f"Slack event processing failed: {message}", flush=True)

        socket_client.socket_mode_request_listeners.append(process_event)
        socket_client.connect()
        with SYNC_LOCK:
            SYNC_STATE.update(status="connected · initial backfill", error=None)
        print("Slack Socket Mode started; initial backfill is running.", flush=True)
        ingest.ingest(readers)
        with SYNC_LOCK:
            if SYNC_STATE.get("status") == "connected · initial backfill":
                SYNC_STATE.update(status="connected · waiting for events", last_sync=time.time() * 1000, error=None)
        print("Initial backfill complete; waiting for Slack events.", flush=True)
        threading.Event().wait()
    except Exception as exc:
        message = f"{exc.__class__.__name__}: {exc}"
        with SYNC_LOCK:
            SYNC_STATE.update(status="socket error", error=message)
        print(f"Slack Socket Mode failed: {message}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        route, query = url.path, parse_qs(url.query)
        if route == "/" or route == "/index.html":
            body, content_type, status = PAGE.encode(), "text/html; charset=utf-8", 200
        elif route == "/skills":
            body = Path(__file__).with_name("skills.html").read_bytes()
            content_type, status = "text/html; charset=utf-8", 200
        elif route == "/api/skills":
            try:
                body = json.dumps(fetch_skills(), default=str).encode()
                content_type, status = "application/json; charset=utf-8", 200
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode()
                content_type, status = "application/json; charset=utf-8", 503
        elif route.startswith("/docs/images/") and route.endswith(".svg"):
            # Only files that really are in docs/images (no path traversal).
            image = Path(__file__).with_name("docs") / "images" / Path(route).name
            if image.is_file():
                body, content_type, status = image.read_bytes(), "image/svg+xml", 200
            else:
                body, content_type, status = b"Not found", "text/plain; charset=utf-8", 404
        elif route == "/how-it-works":
            body = Path(__file__).with_name("how_it_works.html").read_bytes()
            content_type, status = "text/html; charset=utf-8", 200
        elif route == "/api/data":
            try:
                body = json.dumps(fetch_data(platform=(query.get("platform") or [None])[0],
                                             email=(query.get("email") or [None])[0]), default=str).encode()
                content_type, status = "application/json; charset=utf-8", 200
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode()
                content_type, status = "application/json; charset=utf-8", 503
        else:
            body, content_type, status = b"Not found", "text/plain; charset=utf-8", 404
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")


if __name__ == "__main__":
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Message mapping dashboard: http://{host}:{port}")
    if os.getenv("SLACK_AUTO_SYNC", "true").strip().lower() not in {"0", "false", "no", "off"}:
        if os.getenv("SLACK_APP_TOKEN", "").strip():
            print("Slack Socket Mode enabled; performing an initial backfill.", flush=True)
            threading.Thread(target=slack_socket_worker, name="slack-socket", daemon=True).start()
        else:
            interval = max(30, int(os.getenv("SLACK_SYNC_INTERVAL_SECONDS", "60")))
            print(f"SLACK_APP_TOKEN not found; falling back to polling every {interval} seconds.", flush=True)
            threading.Thread(target=slack_sync_worker, name="slack-sync", daemon=True).start()
    else:
        SYNC_STATE["status"] = "disabled"
        print("Automatic Slack sync disabled by SLACK_AUTO_SYNC.", flush=True)
    from integrations import sync
    if all(os.getenv(k, "").strip() for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")):
        interval = max(30, int(os.getenv("JIRA_SYNC_INTERVAL_SECONDS", "60")))
        print(f"Jira configured; polling every {interval} seconds.", flush=True)
        threading.Thread(target=platform_sync_worker, name="jira-sync", daemon=True,
                         args=("jira", lambda cycle: sync.sync_jira(), interval)).start()
    if os.getenv("CONFLUENCE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        interval = max(30, int(os.getenv("CONFLUENCE_SYNC_INTERVAL_SECONDS", "60")))
        print(f"Confluence enabled; polling every {interval} seconds.", flush=True)
        threading.Thread(target=platform_sync_worker, name="confluence-sync", daemon=True,
                         args=("confluence", lambda cycle: sync.sync_confluence(), interval)).start()
    if all(os.getenv(k, "").strip() for k in ("CLICKUP_API_TOKEN", "CLICKUP_TEAM_ID")):
        interval = max(30, int(os.getenv("CLICKUP_SYNC_INTERVAL_SECONDS", "60")))
        print(f"ClickUp configured; polling every {interval} seconds (full re-read every 10th run).", flush=True)
        threading.Thread(target=platform_sync_worker, name="clickup-sync", daemon=True,
                         args=("clickup", lambda cycle: sync.sync_clickup(full=cycle % 10 == 0), interval)).start()
    if os.getenv("SKILLS_PIPELINE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        skills_interval = max(120, int(os.getenv("SKILLS_PIPELINE_INTERVAL_SECONDS", "900")))
        print(f"Skill pipeline (Agents 1-4, local models) every {skills_interval} seconds.", flush=True)
        threading.Thread(target=skills_worker, name="skills", daemon=True, args=(skills_interval,)).start()
    canonical_interval = max(30, int(os.getenv("CANONICAL_REFRESH_SECONDS", "60")))
    print(f"Identity resolution + canonical records refresh every {canonical_interval} seconds.", flush=True)
    threading.Thread(target=canonical_worker, name="canonical", daemon=True, args=(canonical_interval,)).start()
    if os.getenv("WEBHOOK_TOKEN", "").strip():
        webhook_port = int(os.getenv("WEBHOOK_PORT", "8001"))
        webhook_server = ThreadingHTTPServer(("127.0.0.1", webhook_port), WebhookHandler)
        threading.Thread(target=webhook_server.serve_forever, name="webhooks", daemon=True).start()
        threading.Thread(target=webhook_worker, name="webhook-worker", daemon=True).start()
        print(f"Webhook receiver: http://127.0.0.1:{webhook_port}/webhooks/<jira|clickup>/<WEBHOOK_TOKEN> "
              "(expose this port with a tunnel)", flush=True)
    print("Press Ctrl+C to stop.")
    server.serve_forever()
