"""Local posting calendar for publish plans (127.0.0.1 only; stdlib http.server).

Month view with posts as chips (colour = platform, icon = approval/status), drag a chip to
another day to reschedule (time kept), click a chip to edit text/time and approve/reject.
Every change is validated and written straight back to the plan JSON files.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Posting calendar</title>
<style>
:root{--bg:#0d0d10;--card:#16161b;--line:#26262e;--text:#f2f2f5;--dim:#9a9aa6;--acc:#facc15}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line);flex-wrap:wrap}
h1{font-size:17px;margin:0 12px 0 0}button{background:var(--card);color:var(--text);border:1px solid var(--line);
border-radius:8px;padding:6px 12px;cursor:pointer}button:hover{border-color:var(--dim)}
.legend{display:flex;gap:10px;flex-wrap:wrap;color:var(--dim);font-size:12px}.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px}
main{display:grid;grid-template-columns:1fr 340px;min-height:calc(100vh - 58px)}
#grid{display:grid;grid-template-columns:repeat(7,1fr);grid-template-rows:32px;grid-auto-rows:minmax(118px,auto);gap:1px;background:var(--line);padding:1px}
.dow{background:var(--bg);color:var(--dim);font-size:12px;padding:6px 8px;min-height:auto}
.day{background:var(--bg);padding:6px;min-width:0}.day.other{opacity:.45}.day.today .n{color:#000;background:var(--acc);border-radius:6px;padding:0 6px}
.day.over{outline:2px dashed var(--acc);outline-offset:-3px}.n{font-size:12px;color:var(--dim);margin-bottom:4px;display:inline-block}
.chip{display:flex;gap:6px;align-items:center;font-size:12px;border-radius:6px;padding:3px 6px;margin:3px 0;cursor:grab;
background:var(--card);border-left:4px solid var(--c);white-space:nowrap;overflow:hidden}
.chip.done{cursor:pointer;opacity:.7}.chip .t{color:var(--dim)}.chip .x{overflow:hidden;text-overflow:ellipsis}
aside{border-left:1px solid var(--line);padding:16px;overflow:auto}aside h2{font-size:15px;margin:0 0 8px}
textarea{width:100%;min-height:180px;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit}
input{background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:6px;font:inherit}
.row{display:flex;gap:8px;margin:10px 0;flex-wrap:wrap}.muted{color:var(--dim);font-size:12px}.err{color:#f87171}
.ok{color:#4ade80}a{color:var(--acc)}
@media (max-width:900px){main{grid-template-columns:1fr}aside{border-left:0;border-top:1px solid var(--line)}}
</style></head><body>
<header><h1>📅 Posting calendar</h1><button id="prev">←</button><b id="title"></b><button id="next">→</button>
<button id="today">Today</button><div class="legend" id="legend"></div></header>
<main><div id="grid"></div><aside id="side"><h2>Select a post</h2>
<p class="muted">Drag a post to another day to reschedule it (same time). Click a post to edit,
approve or reject. ✅ approved · ⏳ waiting for approval · 📤 published/scheduled on the platform · ⚠️ error.</p>
<div id="upcoming"></div></aside></main>
<script>
const COLORS={youtube:"#ff3b30",tiktok:"#25f4ee",instagram:"#e1306c",facebook:"#1877f2",linkedin:"#0a66c2",
twitter:"#e7e7e7",threads:"#b4b4b4",reddit:"#ff4500",bluesky:"#1185fe",telegram:"#2aabee",discord:"#5865f2",
mastodon:"#6364ff",pinterest:"#e60023"};
let data={entries:[]},cursor=new Date();cursor.setDate(1);
const $=s=>document.querySelector(s);
const esc=s=>(s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const icon=e=>e.status==="error"?"⚠️":["planned"].includes(e.status)?(e.approved?"✅":"⏳"):e.status==="skipped"?"🚫":"📤";
const when=e=>e.schedule&&e.schedule!=="now"?new Date(e.schedule):null;
const ymd=d=>d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0");
const editable=e=>["planned","error"].includes(e.status);
async function load(){data=await (await fetch("/api/plan")).json();render()}
async function act(body){const r=await fetch("/api/update",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
 const j=await r.json();if(!r.ok){alert(j.error||"failed");return null}data=j;render();return j}
function render(){
 const y=cursor.getFullYear(),m=cursor.getMonth();
 $("#title").textContent=cursor.toLocaleString(undefined,{month:"long",year:"numeric"});
 const plats=[...new Set(data.entries.map(e=>e.platform))];
 $("#legend").innerHTML=plats.map(p=>`<span><i style="background:${COLORS[p]||"#888"}"></i>${p}</span>`).join("");
 const g=$("#grid");g.innerHTML=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].map(d=>`<div class="dow">${d}</div>`).join("");
 const first=new Date(y,m,1),start=new Date(first);start.setDate(1-((first.getDay()+6)%7));
 const today=ymd(new Date());
 for(let i=0;i<42;i++){const d=new Date(start);d.setDate(start.getDate()+i);const key=ymd(d);
  const cell=document.createElement("div");cell.className="day"+(d.getMonth()!==m?" other":"")+(key===today?" today":"");
  cell.innerHTML=`<span class="n">${d.getDate()}</span>`;cell.dataset.day=key;
  data.entries.filter(e=>when(e)&&ymd(when(e))===key).sort((a,b)=>when(a)-when(b)).forEach(e=>{
   const c=document.createElement("div");c.className="chip"+(editable(e)?"":" done");c.style.setProperty("--c",COLORS[e.platform]||"#888");
   c.draggable=editable(e);c.title=e.text;
   c.innerHTML=`${icon(e)}<span class="t">${when(e).toTimeString().slice(0,5)}</span><span class="x">${esc(e.platform)} · ${esc(e.title)}</span>`;
   c.onclick=()=>select(e.key);c.ondragstart=ev=>ev.dataTransfer.setData("text",e.key);cell.appendChild(c)});
  cell.ondragover=ev=>{ev.preventDefault();cell.classList.add("over")};cell.ondragleave=()=>cell.classList.remove("over");
  cell.ondrop=async ev=>{ev.preventDefault();cell.classList.remove("over");const k=ev.dataTransfer.getData("text");
   const e=data.entries.find(x=>x.key===k);if(!e)return;const t=when(e);const [Y,M,D]=key.split("-").map(Number);
   const n=new Date(Y,M-1,D,t.getHours(),t.getMinutes());await act({key:k,schedule:localIso(n)})};
  g.appendChild(cell)}
 const now=Date.now(),up=data.entries.filter(e=>!when(e)||when(e)>=now).sort((a,b)=>(when(a)||0)-(when(b)||0)).slice(0,12);
 $("#upcoming").innerHTML="<h2>Upcoming</h2>"+up.map(e=>`<div class="chip" style="--c:${COLORS[e.platform]||"#888"}" onclick="select('${e.key}')">${icon(e)}<span class="t">${when(e)?when(e).toLocaleString():"now"}</span><span class="x">${esc(e.platform)}</span></div>`).join("")}
function localIso(d){const o=-d.getTimezoneOffset(),s=o>=0?"+":"-",p=n=>String(Math.floor(Math.abs(n))).padStart(2,"0");
 return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:00${s}${p(o/60)}:${p(o%60)}`}
function select(key){const e=data.entries.find(x=>x.key===key);if(!e)return;const t=when(e);
 const v=t?new Date(t.getTime()-t.getTimezoneOffset()*60000).toISOString().slice(0,16):"";
 const res=e.result&&e.result.url?`<p>🔗 <a href="${esc(e.result.url)}" target="_blank" rel="noopener">${esc(e.result.url)}</a></p>`:"";
 $("#side").innerHTML=`<h2>${icon(e)} ${esc(e.platform)} · ${esc(e.title)}</h2><p class="muted">${esc(e.id)} · status <b>${esc(e.status)}</b>${e.approved?" · approved":""}<br>${esc(e.plan)}</p>
 ${e.error?`<p class="err">${esc(e.error)}</p>`:""}${res}
 <label class="muted">Time</label><div class="row"><input type="datetime-local" id="tm" value="${v}" ${editable(e)?"":"disabled"}></div>
 <label class="muted">Text (${(e.text||"").length} chars)</label><textarea id="tx" ${editable(e)?"":"disabled"}>${esc(e.text)}</textarea>
 ${editable(e)?`<div class="row"><button id="save">💾 Save</button><button id="ap">${e.approved?"↩️ Unapprove":"✅ Approve"}</button><button id="rj">🚫 Reject</button></div>`:""}
 <p class="muted">Approving here marks the post as OK to publish; it is sent by <code>publish.py run --yes</code> or the scheduler when due.</p>
 <div id="msg"></div><p><button onclick="load().then(()=>location.reload())">← Back</button></p>`;
 if(!editable(e))return;
 $("#save").onclick=async()=>{const tv=$("#tm").value;const r=await act({key,text:$("#tx").value,schedule:tv?localIso(new Date(tv)):"now"});if(r){$("#msg").innerHTML='<p class="ok">Saved</p>'}};
 $("#ap").onclick=async()=>{if(await act({key,approved:!e.approved}))select(key)};
 $("#rj").onclick=async()=>{if(confirm("Reject this post? It will never be published."))if(await act({key,reject:true}))select(key)}}
$("#prev").onclick=()=>{cursor.setMonth(cursor.getMonth()-1);render()};$("#next").onclick=()=>{cursor.setMonth(cursor.getMonth()+1);render()};
$("#today").onclick=()=>{cursor=new Date();cursor.setDate(1);render()};
load();setInterval(()=>{if(!document.activeElement||document.activeElement.tagName==="BODY")load()},20000);
</script></body></html>"""


def _load(paths: list[Path]) -> dict:
    out = []
    for p in paths:
        plan = json.loads(p.read_text())
        for e in plan["entries"]:
            out.append({**e, "key": f"{p}::{e['id']}", "plan": str(p)})
    return {"entries": out}


def _write(path: Path, plan: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def serve(paths: list[Path], port: int = 8790, open_browser: bool = True, validate=None) -> None:
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == "/api/plan":
                with lock:
                    return self._json(200, _load(paths))
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            if self.path != "/api/update" or self.headers.get("Origin", "http://127.0.0.1").split(":")[1] not in ("//127.0.0.1", "//localhost"):
                return self._json(403, {"error": "forbidden"})
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            plan_path, _, eid = req.get("key", "").partition("::")
            p = Path(plan_path)
            if p not in paths:
                return self._json(400, {"error": "unknown plan"})
            with lock:
                plan = json.loads(p.read_text())
                e = next((x for x in plan["entries"] if x["id"] == eid), None)
                if not e:
                    return self._json(404, {"error": "unknown post"})
                if e.get("status") not in ("planned", "error"):
                    return self._json(409, {"error": "already published/scheduled on the platform — edit it there"})
                trial = dict(e)
                if "text" in req:
                    trial["text"] = req["text"]
                if "schedule" in req:
                    trial["schedule"] = req["schedule"] or "now"
                    if trial["schedule"] != "now":
                        t = dt.datetime.fromisoformat(trial["schedule"])
                        if t < dt.datetime.now(t.tzinfo) - dt.timedelta(minutes=1):
                            return self._json(400, {"error": "that time is in the past"})
                if validate and ("text" in req or "schedule" in req):
                    errs = [x for x in validate(trial) if "not connected" not in x and "run `publish.py" not in x]
                    if errs:
                        return self._json(400, {"error": "; ".join(errs)})
                e.update({k: trial[k] for k in ("text", "schedule")})
                if "approved" in req:
                    e["approved"] = bool(req["approved"])
                    e["approved_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds") if e["approved"] else None
                    e["approved_via"] = "calendar"
                if req.get("reject"):
                    e.update(approved=False, status="skipped")
                _write(p, plan)
                return self._json(200, _load(paths))

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}/"
    print(f"Calendar running at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
