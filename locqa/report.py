"""report.json (contract format) + human-readable / reviewable HTML report."""
from __future__ import annotations

import collections
import datetime as _dt
import html
import json
from typing import Dict, List, Optional

from . import __version__
from .checks import ISSUE_TYPES, Finding

CONTRACT_FIELDS = ["key", "issue_type", "severity", "source", "target", "evidence", "suggestion",
                   "confidence", "reviewed_by_human"]
EXTRA_FIELDS = ["line", "section", "detector"]


def finding_dict(f: Finding) -> dict:
    d = f.to_dict()
    return {k: d[k] for k in CONTRACT_FIELDS + EXTRA_FIELDS}


def summary(findings: List[Finding], meta: dict) -> dict:
    by_type = collections.Counter(f.issue_type for f in findings)
    by_sev = collections.Counter(f.severity for f in findings)
    return {
        **meta,
        "total_findings": len(findings),
        "keys_with_findings": len({f.key for f in findings}),
        "by_issue_type": {t: by_type.get(t, 0) for t in ISSUE_TYPES},
        "by_severity": {s: by_sev.get(s, 0) for s in ("high", "medium", "low")},
        "with_suggestion": sum(1 for f in findings if f.suggestion),
        "reviewed_by_human": sum(1 for f in findings if f.reviewed_by_human),
    }


def write_json(path: str, findings: List[Finding], meta: dict, candidates: List[Finding] = (),
               warnings: List[dict] = ()) -> None:
    """``path`` gets a top-level JSON array of findings (exact contract schema + a few
    extra explainability fields). Metadata, candidates and warnings go to a sidecar
    ``<path>.meta.json`` so the main file stays trivially machine-checkable."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([finding_dict(f) for f in findings], fh, ensure_ascii=False, indent=2)
    side = path[:-5] + ".meta.json" if path.endswith(".json") else path + ".meta.json"
    with open(side, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary(findings, meta),
                   "candidates_below_threshold": [finding_dict(f) for f in candidates],
                   "warnings": list(warnings)}, fh, ensure_ascii=False, indent=2)


def write_html(path: str, findings: List[Finding], meta: dict, candidates: List[Finding] = (),
               warnings: List[dict] = ()) -> None:
    data = {
        "meta": summary(findings, meta),
        "findings": [finding_dict(f) for f in findings],
        "candidates": [finding_dict(f) for f in candidates],
        "warnings": list(warnings),
        "types": ISSUE_TYPES,
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"locqa · {html.escape(meta.get('target_name', 'report'))}"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(HTML_TEMPLATE.replace("__TITLE__", title).replace("__DATA__", payload))


HTML_TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f5f1;--panel:#fff;--ink:#1d1f24;--muted:#6a6f7a;--line:#e3e1da;--accent:#0b6e6e;--accent-ink:#fff;
--high:#b42318;--medium:#b54708;--low:#5b6472;--ok:#067647;--hl:#fff3c4;--ph:#e6f0ff;--del:#ffe2e0;--ins:#dcfae6;--chip:#efede7}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121417;--panel:#1a1d21;--ink:#e8e6e1;--muted:#9aa0a9;
--line:#2b3036;--accent:#3fb6b0;--accent-ink:#0d1112;--high:#f97066;--medium:#fdb022;--low:#98a2b3;--ok:#47cd89;--hl:#4a3f12;
--ph:#1d2b45;--del:#4a1f1c;--ins:#133b28;--chip:#24282e}}
:root[data-theme="dark"]{--bg:#121417;--panel:#1a1d21;--ink:#e8e6e1;--muted:#9aa0a9;--line:#2b3036;--accent:#3fb6b0;--accent-ink:#0d1112;
--high:#f97066;--medium:#fdb022;--low:#98a2b3;--ok:#47cd89;--hl:#4a3f12;--ph:#1d2b45;--del:#4a1f1c;--ins:#133b28;--chip:#24282e}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:20px 16px 8px;max-width:1200px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px;font-weight:650;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px}
.wrap{max-width:1200px;margin:0 auto;padding:0 16px 48px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin:14px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.stat b{display:block;font-size:20px;font-variant-numeric:tabular-nums}
.stat span{color:var(--muted);font-size:12px}
.bars{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:12px}
.bar{display:grid;grid-template-columns:200px 1fr 44px;gap:8px;align-items:center;font-size:12.5px;padding:2px 0;cursor:pointer}
.bar>span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar .t{height:10px;background:var(--chip);border-radius:5px;overflow:hidden}
.bar .t i{display:block;height:100%;background:var(--accent)}
.bar .n{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted)}
.bar.on{font-weight:650}
.tools{position:sticky;top:0;z-index:5;background:var(--bg);padding:8px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center;border-bottom:1px solid var(--line)}
input[type=search],select{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:inherit}
input[type=search]{flex:1;min-width:180px}
button{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:6px 10px;cursor:pointer}
button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.progress{font-size:12.5px;color:var(--muted)}
.card{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--low);border-radius:10px;padding:12px 14px;margin:10px 0}
.card.high{border-left-color:var(--high)}.card.medium{border-left-color:var(--medium)}
.card.dec-accept{box-shadow:inset 0 0 0 1px var(--ok)}.card.dec-reject{opacity:.55}
.row1{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin-bottom:8px}
.key{font:600 13px ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}
.chip{font-size:11.5px;background:var(--chip);border-radius:999px;padding:2px 8px;color:var(--muted)}
.sev{font-weight:650;text-transform:uppercase;font-size:11px;letter-spacing:.04em}
.sev.high{color:var(--high)}.sev.medium{color:var(--medium)}.sev.low{color:var(--low)}
.grid{display:grid;grid-template-columns:110px 1fr;gap:4px 10px;font-size:13.5px}
.grid .l{color:var(--muted);font-size:12px;padding-top:2px}
.txt{white-space:pre-wrap;word-break:break-word}
.ph{background:var(--ph);border-radius:3px;padding:0 2px;font-family:ui-monospace,monospace;font-size:12.5px}
del{background:var(--del);text-decoration:line-through;text-decoration-thickness:1px}
ins{background:var(--ins);text-decoration:none}
.ev{color:var(--muted);font-size:12.5px;margin-top:6px}
.actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;align-items:center}
textarea{width:100%;min-height:54px;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit}
.muted{color:var(--muted)}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:10px 0}
summary{cursor:pointer;font-weight:600}
.empty{padding:40px;text-align:center;color:var(--muted)}
@media (max-width:640px){.grid{grid-template-columns:1fr}.grid .l{padding-top:6px}.bar{grid-template-columns:120px 1fr 36px}}
</style>
</head>
<body>
<header>
  <h1 id="h1"></h1>
  <div class="sub" id="sub"></div>
</header>
<div class="wrap">
  <div class="stats" id="stats"></div>
  <div class="bars" id="bars" aria-label="Findings by issue type"></div>
  <div class="tools">
    <input type="search" id="q" placeholder="Search key, text, evidence…" aria-label="Search">
    <select id="sev" aria-label="Severity"><option value="">All severities</option><option>high</option><option>medium</option><option>low</option></select>
    <select id="dec" aria-label="Review state"><option value="">All review states</option><option value="none">Not reviewed</option><option value="accept">Accepted</option><option value="edit">Edited</option><option value="reject">Rejected</option></select>
    <label class="progress"><input type="checkbox" id="cand"> show candidates</label>
    <input id="reviewer" placeholder="Reviewer name" aria-label="Reviewer name" style="width:140px;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 10px">
    <button class="primary" id="export">Export decisions.json</button>
    <label class="progress" style="cursor:pointer"><input type="file" id="import" accept=".json" hidden>Import…</label>
    <span class="progress" id="prog"></span>
  </div>
  <div id="list"></div>
  <details id="warn"><summary>File-level warnings</summary><div id="warnlist"></div></details>
</div>
<script>
const DATA = __DATA__;
const LS = "locqa-decisions:" + (DATA.meta.target_name||"") ;
let decisions = {};
try { decisions = JSON.parse(localStorage.getItem(LS) || "{}"); } catch(e) { decisions = {}; }
try { document.getElementById("reviewer").value = localStorage.getItem("locqa-reviewer") || ""; } catch(e) {}
const state = {type:"", q:"", sev:"", dec:"", cand:false};
const esc = s => (s==null?"":String(s)).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const markPh = s => esc(s).replace(/(\{[^{}]*\}|%\d*\$?[sd]|&lt;\/?\w+&gt;)/g, '<span class="ph">$1</span>');
function diff(a, b){ // word-level LCS diff
  const A=(a||"").split(/(\s+)/), B=(b||"").split(/(\s+)/);
  if (A.length*B.length > 40000) return {a:markPh(a), b:markPh(b)};
  const m=A.length,n=B.length,dp=Array.from({length:m+1},()=>new Int32Array(n+1));
  for(let i=m-1;i>=0;i--)for(let j=n-1;j>=0;j--)dp[i][j]=A[i]===B[j]?dp[i+1][j+1]+1:Math.max(dp[i+1][j],dp[i][j+1]);
  let i=0,j=0,oa="",ob="";
  while(i<m&&j<n){ if(A[i]===B[j]){oa+=markPh(A[i]);ob+=markPh(B[j]);i++;j++;}
    else if(dp[i+1][j]>=dp[i][j+1]){oa+="<del>"+esc(A[i])+"</del>";i++;} else {ob+="<ins>"+esc(B[j])+"</ins>";j++;} }
  while(i<m){oa+="<del>"+esc(A[i])+"</del>";i++;} while(j<n){ob+="<ins>"+esc(B[j])+"</ins>";j++;}
  return {a:oa,b:ob};
}
function save(){ try{ localStorage.setItem(LS, JSON.stringify(decisions)); }catch(e){} progress(); }
function progress(){
  const keys = new Set(DATA.findings.filter(f=>f.suggestion||true).map(f=>f.key));
  const done = [...keys].filter(k=>decisions[k]).length;
  document.getElementById("prog").textContent = `${done} / ${keys.size} keys reviewed`;
}
function header(){
  const m=DATA.meta;
  document.getElementById("h1").textContent = `Localization QA — ${m.target_name||""}`;
  document.getElementById("sub").textContent = `${m.source_name} → ${m.target_name} · ${m.strings_checked} strings · locqa ${m.tool_version} · ${m.generated_at}`;
  const s=[["Findings",m.total_findings],["Keys affected",m.keys_with_findings],["High",m.by_severity.high],["Medium",m.by_severity.medium],["Low",m.by_severity.low],["With fix",m.with_suggestion]];
  document.getElementById("stats").innerHTML = s.map(([l,v])=>`<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
  const max=Math.max(1,...Object.values(m.by_issue_type));
  const bars=document.getElementById("bars");
  bars.innerHTML = `<div class="bar ${state.type?"":"on"}" data-t=""><span>all types</span><span class="t"><i style="width:100%"></i></span><span class="n">${m.total_findings}</span></div>` +
    DATA.types.map(t=>`<div class="bar ${state.type===t?"on":""}" data-t="${t}" role="button" tabindex="0"><span>${t}</span><span class="t"><i style="width:${100*(m.by_issue_type[t]||0)/max}%"></i></span><span class="n">${m.by_issue_type[t]||0}</span></div>`).join("");
  bars.querySelectorAll(".bar").forEach(b=>{ const go=()=>{state.type=b.dataset.t;header();render();}; b.onclick=go; b.onkeydown=e=>{if(e.key==="Enter")go();}; });
}
function render(){
  const pool = DATA.findings.concat(state.cand ? DATA.candidates.map(c=>Object.assign({_cand:true},c)) : []);
  const q=state.q.toLowerCase();
  const items = pool.filter(f => (!state.type||f.issue_type===state.type) && (!state.sev||f.severity===state.sev)
     && (!q || [f.key,f.source,f.target,f.evidence,f.suggestion].join(" ").toLowerCase().includes(q))
     && (!state.dec || (state.dec==="none" ? !decisions[f.key] : (decisions[f.key]||{}).verdict===state.dec)));
  const list=document.getElementById("list");
  if(!items.length){ list.innerHTML='<div class="empty">Nothing matches these filters.</div>'; return; }
  list.innerHTML = items.slice(0,600).map((f,idx)=>{
    const d = decisions[f.key]; const fix = d && d.text!=null ? d.text : f.suggestion;
    const df = fix!=null ? diff(f.target, fix) : {a:markPh(f.target), b:""};
    return `<div class="card ${f.severity} ${d?"dec-"+(d.verdict==="edit"?"accept":d.verdict):""}" data-i="${idx}">
      <div class="row1"><span class="key">${esc(f.key)}</span><span class="sev ${f.severity}">${f.severity}</span>
      <span class="chip">${f.issue_type}</span><span class="chip">conf ${Number(f.confidence).toFixed(2)}</span>
      ${f.line?`<span class="chip">line ${f.line}</span>`:""}${f.section?`<span class="chip">§ ${esc(f.section)}</span>`:""}
      ${f._cand?'<span class="chip">candidate</span>':""}${d?`<span class="chip">✔ ${d.verdict}${d.reviewer?" · "+esc(d.reviewer):""}</span>`:""}</div>
      <div class="grid"><div class="l">source (ru)</div><div class="txt">${markPh(f.source)}</div>
      <div class="l">target</div><div class="txt">${df.a||'<span class="muted">(empty / missing)</span>'}</div>
      ${fix!=null?`<div class="l">suggestion</div><div class="txt">${df.b}</div>`:""}</div>
      <div class="ev">${esc(f.evidence)} <span class="muted">· ${esc(f.detector)}</span></div>
      <div class="actions">
        ${f.suggestion!=null?`<button data-a="accept">Accept fix</button>`:""}
        <button data-a="edit">Edit…</button><button data-a="reject">False positive</button>
        ${d?`<button data-a="clear">Undo</button>`:""}
      </div><div class="ed"></div></div>`; }).join("") + (items.length>600?`<div class="empty">Showing 600 of ${items.length}. Narrow the filters.</div>`:"");
  list.querySelectorAll(".card").forEach(card=>{
    const f=items[+card.dataset.i];
    card.querySelectorAll("button[data-a]").forEach(b=>b.onclick=()=>act(f,b.dataset.a,card));
  });
}
function reviewer(){ const r=document.getElementById("reviewer").value.trim(); try{localStorage.setItem("locqa-reviewer",r);}catch(e){} return r; }
function act(f,a,card){
  if(a==="clear"){ delete decisions[f.key]; save(); render(); return; }
  if(a==="accept"){ decisions[f.key]={verdict:"accept",text:f.suggestion,issue_type:f.issue_type,reviewer:reviewer(),ts:new Date().toISOString()}; save(); render(); return; }
  if(a==="reject"){ decisions[f.key]={verdict:"reject",text:null,issue_type:f.issue_type,reviewer:reviewer(),ts:new Date().toISOString()}; save(); render(); return; }
  const ed=card.querySelector(".ed"); const cur=(decisions[f.key]||{}).text ?? f.suggestion ?? f.target;
  ed.innerHTML=`<textarea aria-label="Corrected text">${esc(cur)}</textarea><div class="actions"><button class="primary">Save edit</button><span class="muted">Keep placeholders like {name} intact.</span></div>`;
  const ta=ed.querySelector("textarea"); ta.focus();
  ed.querySelector("button").onclick=()=>{ decisions[f.key]={verdict:"edit",text:ta.value,issue_type:f.issue_type,reviewer:reviewer(),ts:new Date().toISOString()}; save(); render(); };
}
document.getElementById("q").oninput=e=>{state.q=e.target.value;render();};
document.getElementById("sev").onchange=e=>{state.sev=e.target.value;render();};
document.getElementById("dec").onchange=e=>{state.dec=e.target.value;render();};
document.getElementById("cand").onchange=e=>{state.cand=e.target.checked;render();};
document.getElementById("export").onclick=()=>{
  const blob=new Blob([JSON.stringify({target:DATA.meta.target_name,exported_at:new Date().toISOString(),decisions},null,2)],{type:"application/json"});
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download="decisions.json"; a.click();
};
document.getElementById("import").onchange=e=>{ const file=e.target.files[0]; if(!file)return; file.text().then(t=>{ try{ const j=JSON.parse(t); Object.assign(decisions, j.decisions||j); save(); render(); }catch(err){ alert("Not a decisions.json file"); } }); };
const wl=document.getElementById("warnlist");
wl.innerHTML = DATA.warnings.length ? DATA.warnings.map(w=>`<div class="ev"><span class="key">${esc(w.key)}</span> · ${esc(w.type)} · ${esc(w.message)}</div>`).join("") : '<div class="ev">none</div>';
header(); render(); progress();
</script>
</body>
</html>
"""
