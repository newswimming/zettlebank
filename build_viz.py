"""Build edgematrix-viz.html with live graph data embedded inline."""
import json, urllib.request
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).parent

# ── Load graph ────────────────────────────────────────────────────────────────

graph_json = ROOT / "zettlebank-server/_internal/vault_graph.json"
g = json.loads(graph_json.read_text(encoding="utf-8"))
raw_nodes = g["nodes"]
raw_edges = g.get("edges") or g.get("links", [])

# ── Community data from server ────────────────────────────────────────────────

resp = urllib.request.urlopen("http://127.0.0.1:8000/graph/communities/multi", timeout=10)
comm = json.loads(resp.read())
macro_map    = comm["macro"]["communities"]   # note_id -> comm_id
macro_labels = comm["macro"]["labels"]        # str(comm_id) -> label str

# ── Assign Kishōtenketsu act by community size ────────────────────────────────

sizes = Counter(macro_map.values())
acts = {}
for cid, sz in sizes.items():
    if sz >= 7:   acts[str(cid)] = "ki"
    elif sz >= 4: acts[str(cid)] = "sho"
    elif sz >= 2: acts[str(cid)] = "ten"
    else:         acts[str(cid)] = "ketsu"

# ── Enrich nodes ──────────────────────────────────────────────────────────────

degree = Counter()
for e in raw_edges:
    degree[e["source"]] += 1
    degree[e["target"]] += 1

enriched = []
for n in raw_nodes:
    nid  = n["id"]
    cid  = macro_map.get(nid, -1)
    lbl  = macro_labels.get(str(cid), f"comm {cid}")
    act  = acts.get(str(cid), "ketsu")
    enriched.append({
        "id":         nid,
        "label":      nid.replace("-", " ").title()[:32],
        "tags":       n.get("tags", []),
        "comm":       cid,
        "act":        act,
        "deg":        degree[nid],
        "comm_label": lbl,
    })

data = {
    "nodes": enriched,
    "edges": raw_edges,
    "stats": {
        "nodes":             len(enriched),
        "edges":             len(raw_edges),
        "macro_communities": len(sizes),
    },
}

data_js = "const GRAPH_DATA = " + json.dumps(data, ensure_ascii=False) + ";"

# ── HTML template ─────────────────────────────────────────────────────────────

template = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>ZettleBank \u2014 Edge Matrix</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f1117;color:#e2e8f0;font-family:'Segoe UI',system-ui,sans-serif;display:flex;flex-direction:column;height:100vh;overflow:hidden}
header{padding:10px 18px;background:#1a1d27;border-bottom:1px solid #2d3148;display:flex;align-items:center;gap:16px;flex-shrink:0;flex-wrap:wrap}
header h1{font-size:14px;font-weight:700;color:#a5b4fc}
.stat{font-size:11px;color:#64748b}.stat b{color:#94a3b8}
.main{display:flex;flex:1;overflow:hidden}
#graph-wrap{flex:1;position:relative;overflow:hidden;background:#0a0c14}
svg{width:100%;height:100%}
.sidebar{width:300px;flex-shrink:0;background:#1a1d27;border-left:1px solid #2d3148;overflow-y:auto;font-size:12px}
.panel{border-bottom:1px solid #2d3148;padding:12px 14px}
.panel h2{font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#64748b;margin-bottom:8px}
.lr{display:flex;align-items:flex-start;gap:8px;margin-bottom:7px}
.sw{width:11px;height:11px;border-radius:50%;flex-shrink:0;margin-top:1px}
.dsw{width:26px;height:2px;flex-shrink:0;margin-top:5px;border-radius:1px}
.lbl{color:#cbd5e1;font-weight:600;font-size:11px}.desc{color:#64748b;font-size:10px;margin-top:1px;line-height:1.4}
#tt{position:absolute;pointer-events:none;background:#1e2235;border:1px solid #3b4168;border-radius:7px;padding:10px 12px;font-size:11px;line-height:1.6;max-width:270px;opacity:0;transition:opacity .12s;z-index:10;box-shadow:0 8px 24px rgba(0,0,0,.6)}
#tt.on{opacity:1}
.tt-h{font-weight:700;color:#a5b4fc;margin-bottom:5px;font-size:12px}
.tr{display:flex;gap:6px}.tk{color:#64748b;min-width:82px;flex-shrink:0}.tv{color:#e2e8f0}
.badge{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:700}
#info{display:none;padding:12px 14px;border-bottom:1px solid #2d3148}
#info h2{font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#6366f1;margin-bottom:8px}
.if{margin-bottom:5px;font-size:11px}.ik{color:#64748b;display:inline-block;min-width:90px}.iv{color:#e2e8f0}
.tagpill{display:inline-block;background:#1e2a3a;color:#60a5fa;padding:1px 5px;border-radius:3px;font-size:10px;margin:1px}
.ctrl{position:absolute;bottom:14px;left:12px;display:flex;gap:6px}
.btn{background:#1e2235;border:1px solid #3b4168;color:#94a3b8;font-size:11px;padding:5px 11px;border-radius:5px;cursor:pointer}
.btn:hover{background:#2d3148;color:#e2e8f0}.btn.active{background:#6366f1;border-color:#6366f1;color:#fff}
#search-wrap{padding:10px 14px;border-bottom:1px solid #2d3148}
#search{width:100%;background:#0f1117;border:1px solid #2d3148;color:#e2e8f0;padding:5px 9px;border-radius:5px;font-size:11px}
#search:focus{outline:none;border-color:#6366f1}
#search-results{margin-top:6px;max-height:120px;overflow-y:auto}
.sr{padding:3px 4px;border-radius:3px;cursor:pointer;color:#94a3b8;font-size:10px}.sr:hover{background:#2d3148;color:#e2e8f0}
#act-filter{display:flex;gap:4px;flex-wrap:wrap;padding:8px 14px;border-bottom:1px solid #2d3148}
.af{padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700;cursor:pointer;opacity:.4;border:1px solid transparent;user-select:none}.af.on{opacity:1}
</style>
</head>
<body>
<header>
  <h1>ZettleBank \u00b7 Edge Matrix</h1>
  <div class="stat"><b id="s-nodes">\u2014</b> nodes</div>
  <div class="stat"><b id="s-edges">\u2014</b> edges</div>
  <div class="stat"><b id="s-comms">\u2014</b> macro communities (\u03b3=1.0)</div>
  <div class="stat" style="margin-left:auto;color:#4b5563">choracle-remote-01</div>
</header>
<div class="main">
<div id="graph-wrap">
  <svg id="svg"></svg>
  <div id="tt"></div>
  <div class="ctrl">
    <button class="btn" onclick="resetZoom()">Reset view</button>
    <button class="btn active" id="btn-hulls"   onclick="toggleHulls()">Hulls</button>
    <button class="btn active" id="btn-labels"  onclick="toggleLabels()">Labels</button>
  </div>
</div>
<div class="sidebar">
  <div id="info"><h2 id="info-type">Selected</h2><div id="info-body"></div></div>
  <div id="search-wrap">
    <input id="search" placeholder="Search notes\u2026" oninput="doSearch(this.value)">
    <div id="search-results"></div>
  </div>
  <div id="act-filter">
    <div class="af on" data-act="ki"    style="background:#34d39922;color:#34d399;border-color:#34d399" onclick="toggleAct('ki')">\u8d77 ki</div>
    <div class="af on" data-act="sho"   style="background:#60a5fa22;color:#60a5fa;border-color:#60a5fa" onclick="toggleAct('sho')">\u627f sho</div>
    <div class="af on" data-act="ten"   style="background:#f59e0b22;color:#f59e0b;border-color:#f59e0b" onclick="toggleAct('ten')">\u8ee2 ten</div>
    <div class="af on" data-act="ketsu" style="background:#c084fc22;color:#c084fc;border-color:#c084fc" onclick="toggleAct('ketsu')">\u7d50 ketsu</div>
  </div>
  <div class="panel">
    <h2>Narrative Act <span style="color:#4b5563">(narrative_act)</span></h2>
    <p style="font-size:10px;color:#4b5563;margin-bottom:8px">Kishōtenketsu act inferred from Leiden community size. Richer assignment arrives after /analyze runs on individual notes.</p>
    <div class="lr"><div class="sw" style="background:#34d399"></div><div><div class="lbl">ki \u8d77 \u2014 Introduction</div><div class="desc">Largest clusters (\u22657 notes). Foundational, well-connected hubs. Low Burt constraint.</div></div></div>
    <div class="lr"><div class="sw" style="background:#60a5fa"></div><div><div class="lbl">sho \u627f \u2014 Development</div><div class="desc">Medium clusters (4\u20136 notes). Build on and extend ki themes.</div></div></div>
    <div class="lr"><div class="sw" style="background:#f59e0b"></div><div><div class="lbl">ten \u8ee2 \u2014 Twist</div><div class="desc">Small clusters (2\u20133 notes). Structural bridges, unexpected pivots. High Burt constraint.</div></div></div>
    <div class="lr"><div class="sw" style="background:#c084fc"></div><div><div class="lbl">ketsu \u7d50 \u2014 Resolution</div><div class="desc">Singleton clusters. Peripheral; awaiting connection via /analyze.</div></div></div>
  </div>
  <div class="panel">
    <h2>Relation Type <span style="color:#4b5563">(relation_type)</span></h2>
    <p style="font-size:10px;color:#4b5563;margin-bottom:8px">All current edges are <em>related</em> (wiki-link default). Richer types appear after /analyze runs.</p>
    <div class="lr"><div class="dsw" style="background:#34d399"></div><div><div class="lbl">supports</div><div class="desc">Bidirectional edge or similarity &gt; 0.8.</div></div></div>
    <div class="lr"><div class="dsw" style="background:#f87171"></div><div><div class="lbl">contradicts</div><div class="desc">LLM-classified tension.</div></div></div>
    <div class="lr"><div class="dsw" style="background:#60a5fa"></div><div><div class="lbl">potential_to</div><div class="desc">One-way forward edge \u2014 latent possibility.</div></div></div>
    <div class="lr"><div class="dsw" style="background:#22d3ee"></div><div><div class="lbl">kinetic_to</div><div class="desc">Target\u2019s SC outlinks reference source.</div></div></div>
    <div class="lr"><div class="dsw" style="background:#fbbf24"></div><div><div class="lbl">motivates</div><div class="desc">Similarity 0.5\u20130.8.</div></div></div>
    <div class="lr"><div class="dsw" style="background:#fb923c"></div><div><div class="lbl">hinders</div><div class="desc">Source impedes target (LLM-classified).</div></div></div>
    <div class="lr"><div class="dsw" style="background:#94a3b8"></div><div><div class="lbl">related</div><div class="desc">Default \u2014 wiki-link or similarity &lt; 0.5.</div></div></div>
  </div>
  <div class="panel">
    <h2>Provenance <span style="color:#4b5563">(provenance)</span></h2>
    <div class="lr"><div class="dsw" style="background:#a5b4fc"></div><div><div class="lbl">sc_embedding</div><div class="desc">Solid line \u2014 Smart Connections cosine similarity.</div></div></div>
    <div class="lr"><div style="width:26px;border-top:2px dashed #a5b4fc;margin-top:5px;flex-shrink:0"></div><div><div class="lbl">wikilink</div><div class="desc">Dashed \u2014 <code style="color:#818cf8">[[wiki-link]]</code> from note body.</div></div></div>
    <div class="lr"><div style="width:26px;border-top:2px dotted #a5b4fc;margin-top:5px;flex-shrink:0"></div><div><div class="lbl">llm</div><div class="desc">Dotted \u2014 LLM-inferred relation.</div></div></div>
  </div>
  <div class="panel">
    <h2>Confidence <span style="color:#4b5563">(confidence)</span></h2>
    <p style="font-size:10px;color:#64748b">Rendered as edge <strong>opacity</strong>. SC scores map directly; wiki-links default to 0.5; LLM edges carry explicit probability.</p>
  </div>
  <div class="panel">
    <h2>Node Size</h2>
    <p style="font-size:10px;color:#64748b">Proportional to <strong>degree</strong> (in + out edges). Larger = more connected in the vault.</p>
  </div>
</div>
</div>
<script>
%%DATA_JS%%

const {nodes:RAW_NODES, edges:RAW_EDGES, stats:STATS} = GRAPH_DATA;
const ACT_COLOR={ki:'#34d399',sho:'#60a5fa',ten:'#f59e0b',ketsu:'#c084fc'};
const ACT_DESC ={ki:'\u8d77 Introduction',sho:'\u627f Development',ten:'\u8ee2 Twist',ketsu:'\u7d50 Resolution'};
const REL_COLOR={supports:'#34d399',contradicts:'#f87171',potential_to:'#60a5fa',kinetic_to:'#22d3ee',motivates:'#fbbf24',hinders:'#fb923c',related:'#94a3b8'};
const PROV_DASH={sc_embedding:'none',wikilink:'6,4',llm:'2,3'};

let showHulls=true,showLabels=true,activeActs=new Set(['ki','sho','ten','ketsu']);

document.getElementById('s-nodes').textContent=STATS.nodes;
document.getElementById('s-edges').textContent=STATS.edges;
document.getElementById('s-comms').textContent=STATS.macro_communities;

const wrap=document.getElementById('graph-wrap');
let W=wrap.clientWidth,H=wrap.clientHeight;
const svg=d3.select('#svg');
const g=svg.append('g');
const zoom=d3.zoom().scaleExtent([0.1,8]).on('zoom',e=>g.attr('transform',e.transform));
svg.call(zoom);
function resetZoom(){svg.transition().duration(600).call(zoom.transform,d3.zoomIdentity.translate(W/2,H/2).scale(0.85));}

const defs=svg.append('defs');
Object.entries(REL_COLOR).forEach(([r,c])=>{
  defs.append('marker').attr('id','arr-'+r).attr('viewBox','0 -5 10 10')
    .attr('refX',16).attr('refY',0).attr('markerWidth',5).attr('markerHeight',5).attr('orient','auto')
    .append('path').attr('d','M0,-5L10,0L0,5').attr('fill',c);
});

const hullLayer=g.append('g');
const lblLayer=g.append('g');
const edgeLayer=g.append('g');
const nodeLayer=g.append('g');

const nodes=RAW_NODES.map(d=>({...d}));
const byId=Object.fromEntries(nodes.map(n=>[n.id,n]));
const links=RAW_EDGES.map(e=>({...e,source:byId[e.source]||e.source,target:byId[e.target]||e.target}));
const maxDeg=d3.max(nodes,d=>d.deg)||1;
const r=d=>5+Math.sqrt(d.deg/maxDeg)*13;

const sim=d3.forceSimulation(nodes)
  .force('link',d3.forceLink(links).id(d=>d.id).distance(d=>{
    const s=d.source,t=d.target;
    return s.comm===t.comm?70:190;
  }).strength(0.7))
  .force('charge',d3.forceManyBody().strength(-200))
  .force('center',d3.forceCenter(0,0))
  .force('collide',d3.forceCollide(d=>r(d)+5))
  .force('cluster',alpha=>{
    const cx={},cy={},cn={};
    nodes.forEach(n=>{cx[n.comm]=(cx[n.comm]||0)+n.x;cy[n.comm]=(cy[n.comm]||0)+n.y;cn[n.comm]=(cn[n.comm]||0)+1});
    nodes.forEach(n=>{
      if((cn[n.comm]||0)<2)return;
      n.vx+=((cx[n.comm]/cn[n.comm])-n.x)*alpha*0.14;
      n.vy+=((cy[n.comm]/cn[n.comm])-n.y)*alpha*0.14;
    });
  });

// edges
const eSel=edgeLayer.selectAll('.e').data(links).join('g').attr('class','e')
  .style('cursor','pointer')
  .on('click',(e,d)=>{e.stopPropagation();showEdgeInfo(d)})
  .on('mouseover',(e,d)=>showTT(e,eTTHtml(d))).on('mousemove',moveTT).on('mouseout',hideTT);
const eLine=eSel.append('line')
  .attr('stroke',d=>REL_COLOR[d.relation_type]||'#94a3b8')
  .attr('stroke-width',1.5)
  .attr('stroke-opacity',d=>0.3+(d.confidence||0.5)*0.5)
  .attr('stroke-dasharray',d=>PROV_DASH[d.provenance]||'none')
  .attr('marker-end',d=>'url(#arr-'+d.relation_type+')');

// nodes
const nSel=nodeLayer.selectAll('.n').data(nodes).join('g').attr('class','n')
  .style('cursor','pointer')
  .call(d3.drag()
    .on('start',(e,d)=>{if(!e.active)sim.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y})
    .on('drag',(e,d)=>{d.fx=e.x;d.fy=e.y})
    .on('end',(e,d)=>{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null}))
  .on('click',(e,d)=>{e.stopPropagation();showNodeInfo(d);highlight(d.id)})
  .on('mouseover',(e,d)=>showTT(e,nTTHtml(d))).on('mousemove',moveTT).on('mouseout',hideTT);

nSel.append('circle')
  .attr('r',d=>r(d))
  .attr('fill',d=>ACT_COLOR[d.act])
  .attr('fill-opacity',0.8)
  .attr('stroke',d=>ACT_COLOR[d.act])
  .attr('stroke-width',1.5).attr('stroke-opacity',0.9);

const lSel=nSel.append('text')
  .attr('text-anchor','middle')
  .attr('dy',d=>r(d)+10)
  .attr('fill','#94a3b8')
  .style('font-size','9px').style('pointer-events','none')
  .text(d=>d.label.length>24?d.label.slice(0,23)+'\u2026':d.label);

sim.on('tick',()=>{
  eLine.attr('x1',d=>d.source.x).attr('y1',d=>d.source.y)
       .attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);
  nSel.attr('transform',d=>`translate(${d.x},${d.y})`);
  drawHulls();
});

sim.on('end',()=>resetZoom());

function drawHulls(){
  if(!showHulls){hullLayer.selectAll('*').remove();lblLayer.selectAll('*').remove();return;}
  const grps=d3.group(nodes,d=>d.comm);
  const hd=[],ld=[];
  grps.forEach((ms,comm)=>{
    const act=ms[0].act;
    const cy2=d3.mean(ms,n=>n.y)-d3.max(ms,n=>Math.abs(n.y-d3.mean(ms,m=>m.y)))-22;
    ld.push({comm,act,cx:d3.mean(ms,n=>n.x),cy:cy2});
    const pts=[];
    ms.forEach(n=>{const rad=r(n)+14;for(let a=0;a<2*Math.PI;a+=Math.PI/8)pts.push([n.x+rad*Math.cos(a),n.y+rad*Math.sin(a)])});
    const hull=pts.length>=3?d3.polygonHull(pts):null;
    if(hull)hd.push({comm,act,hull,single:null});
    else hd.push({comm,act,hull:null,single:ms[0]});
  });
  hullLayer.selectAll('path.ch').data(hd,d=>d.comm).join('path').attr('class','ch')
    .attr('fill',d=>ACT_COLOR[d.act]).attr('fill-opacity',0.055)
    .attr('stroke',d=>ACT_COLOR[d.act]).attr('stroke-opacity',0.18).attr('stroke-width',1.5)
    .attr('d',d=>{
      if(d.single){const n=d.single,rad=r(n)+16;return `M${n.x},${n.y-rad}A${rad},${rad} 0 1,1 ${n.x-.01},${n.y-rad}Z`;}
      return 'M'+d.hull.join('L')+'Z';
    });
  lblLayer.selectAll('text.al').data(ld,d=>d.comm).join('text').attr('class','al')
    .attr('x',d=>d.cx).attr('y',d=>d.cy).attr('text-anchor','middle')
    .attr('fill',d=>ACT_COLOR[d.act]).attr('font-size',10).attr('font-weight',700)
    .attr('opacity',0.55).attr('pointer-events','none')
    .text(d=>ACT_DESC[d.act]);
}

function toggleHulls(){showHulls=!showHulls;document.getElementById('btn-hulls').classList.toggle('active',showHulls);if(!showHulls){hullLayer.selectAll('*').remove();lblLayer.selectAll('*').remove();}}
function toggleLabels(){showLabels=!showLabels;document.getElementById('btn-labels').classList.toggle('active',showLabels);lSel.attr('display',showLabels?null:'none');}

function toggleAct(act){
  if(activeActs.has(act))activeActs.delete(act);else activeActs.add(act);
  document.querySelector(`.af[data-act="${act}"]`).classList.toggle('on');
  applyFilter();
}
function applyFilter(){
  nSel.attr('opacity',d=>activeActs.has(d.act)?1:0.06);
  eSel.attr('opacity',d=>{
    const sa=d.source.act||'ketsu',ta=d.target.act||'ketsu';
    return(activeActs.has(sa)&&activeActs.has(ta))?1:0.04;
  });
}

function highlight(id){
  const nb=new Set([id]);
  links.forEach(l=>{
    if((l.source.id||l.source)===id)nb.add(l.target.id||l.target);
    if((l.target.id||l.target)===id)nb.add(l.source.id||l.source);
  });
  nSel.attr('opacity',d=>nb.has(d.id)?1:0.1);
  eSel.attr('opacity',d=>((d.source.id||d.source)===id||(d.target.id||d.target)===id)?1:0.04);
}

svg.on('click',()=>{
  nSel.attr('opacity',1);eSel.attr('opacity',1);applyFilter();
  document.getElementById('info').style.display='none';
});

const tt=document.getElementById('tt');
function showTT(e,html){tt.innerHTML=html;tt.classList.add('on');moveTT(e)}
function moveTT(e){tt.style.left=(e.clientX+14)+'px';tt.style.top=(e.clientY-8)+'px'}
function hideTT(){tt.classList.remove('on')}

function nTTHtml(d){
  const c=ACT_COLOR[d.act];
  return`<div class="tt-h">${d.label}</div>
<div class="tr"><span class="tk">note_id</span><span class="tv" style="font-size:9px;word-break:break-all">${d.id}</span></div>
<div class="tr"><span class="tk">act</span><span class="tv"><span class="badge" style="background:${c}22;color:${c}">${d.act} \u00b7 ${ACT_DESC[d.act]}</span></span></div>
<div class="tr"><span class="tk">community</span><span class="tv" style="font-size:9px">${d.comm_label}</span></div>
<div class="tr"><span class="tk">degree</span><span class="tv">${d.deg}</span></div>
<div class="tr"><span class="tk">tags</span><span class="tv" style="font-size:9px">${d.tags.slice(0,3).join(', ')||'\u2014'}</span></div>`}

function eTTHtml(d){
  const c=REL_COLOR[d.relation_type]||'#94a3b8';
  const ac=ACT_COLOR[d.narrative_act]||'#94a3b8';
  const src=d.source.id||d.source,tgt=d.target.id||d.target;
  return`<div class="tt-h">Edge</div>
<div class="tr"><span class="tk">source</span><span class="tv" style="font-size:9px">${src}</span></div>
<div class="tr"><span class="tk">target_id</span><span class="tv" style="font-size:9px">${tgt}</span></div>
<div class="tr"><span class="tk">relation_type</span><span class="tv"><span class="badge" style="background:${c}22;color:${c}">${d.relation_type}</span></span></div>
<div class="tr"><span class="tk">narrative_act</span><span class="tv"><span class="badge" style="background:${ac}22;color:${ac}">${d.narrative_act}</span></span></div>
<div class="tr"><span class="tk">confidence</span><span class="tv">${(d.confidence||0.5).toFixed(2)}</span></div>
<div class="tr"><span class="tk">provenance</span><span class="tv">${d.provenance}</span></div>`}

const info=document.getElementById('info');
function showNodeInfo(d){
  info.style.display='block';
  document.getElementById('info-type').textContent='Node';
  const c=ACT_COLOR[d.act];
  document.getElementById('info-body').innerHTML=`
<div class="if"><span class="ik">note_id</span><span class="iv" style="word-break:break-all;font-size:10px">${d.id}</span></div>
<div class="if"><span class="ik">act</span><span class="badge" style="background:${c}22;color:${c}">${d.act} \u00b7 ${ACT_DESC[d.act]}</span></div>
<div class="if"><span class="ik">community</span><span class="iv" style="font-size:10px">${d.comm_label}</span></div>
<div class="if"><span class="ik">degree</span><span class="iv">${d.deg}</span></div>
<div class="if" style="margin-top:6px"><span class="ik" style="display:block;margin-bottom:3px">tags</span>${
  d.tags.map(t=>`<span class="tagpill">${t}</span>`).join('')||'<span style="color:#4b5563">none</span>'}</div>`;}

function showEdgeInfo(d){
  info.style.display='block';
  document.getElementById('info-type').textContent='Edge (EdgeMatrix)';
  const c=REL_COLOR[d.relation_type]||'#94a3b8';
  const ac=ACT_COLOR[d.narrative_act]||'#94a3b8';
  const src=d.source.id||d.source,tgt=d.target.id||d.target,conf=d.confidence||0.5;
  document.getElementById('info-body').innerHTML=`
<div class="if"><span class="ik">source</span><span class="iv" style="font-size:10px;word-break:break-all">${src}</span></div>
<div class="if"><span class="ik">target_id</span><span class="iv" style="font-size:10px;word-break:break-all">${tgt}</span></div>
<div class="if" style="margin-top:5px"><span class="ik">relation_type</span><span class="badge" style="background:${c}22;color:${c}">${d.relation_type}</span></div>
<div class="if"><span class="ik">narrative_act</span><span class="badge" style="background:${ac}22;color:${ac}">${d.narrative_act} \u00b7 ${ACT_DESC[d.narrative_act]}</span></div>
<div class="if"><span class="ik">confidence</span><span class="iv">${conf.toFixed(2)}</span>
  <div style="background:#1e2a3a;height:5px;border-radius:3px;margin-top:3px"><div style="background:${c};height:5px;border-radius:3px;width:${conf*100}%"></div></div></div>
<div class="if"><span class="ik">provenance</span><span class="iv">${d.provenance}</span></div>
<div class="if"><span class="ik">weight</span><span class="iv">${d.weight||0.5}</span></div>`;}

function doSearch(q){
  const el=document.getElementById('search-results');
  if(!q){el.innerHTML='';return;}
  const hits=nodes.filter(n=>n.id.includes(q.toLowerCase())||n.label.toLowerCase().includes(q.toLowerCase())).slice(0,12);
  el.innerHTML=hits.map(n=>`<div class="sr" onclick="focusNode('${n.id.replace(/'/g,"\\'")}')">${n.label}</div>`).join('');}

function focusNode(id){
  const n=byId[id];if(!n)return;
  document.getElementById('search').value='';document.getElementById('search-results').innerHTML='';
  showNodeInfo(n);highlight(id);
  svg.transition().duration(600).call(zoom.transform,d3.zoomIdentity.translate(W/2,H/2).scale(2.5).translate(-n.x,-n.y));}

window.addEventListener('resize',()=>{W=wrap.clientWidth;H=wrap.clientHeight;sim.force('center',d3.forceCenter(0,0)).alpha(0.05).restart();});
svg.call(zoom.transform,d3.zoomIdentity.translate(W/2,H/2));
</script>
</body>
</html>"""

html = template.replace("%%DATA_JS%%", data_js)
out_path = ROOT / "edgematrix-viz.html"
out_path.write_text(html, encoding="utf-8")
print(f"Written {len(html):,} chars to {out_path}")
