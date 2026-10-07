"""Local pitch-keypoint labelling tool → ``pv-pitch-keypoints/v1`` (the format convert.py / eval read).

    uv run python training/keypoints/convert.py frames --sp09 --out-dir training/keypoints/data/frames
    uv run python training/keypoints/label_tool.py --frames training/keypoints/data/frames \
        --out training/keypoints/data/kp/ours.json          # → http://127.0.0.1:8765

Click a landmark on the pitch diagram, then where it is on the frame. From 4 points the tool fits a homography and
draws the whole pitch over the frame (RMSE in px): a wrong click shows at once. Aim for 6+ well-spread points;
``convert.py yolo --complete-from-h`` fills in the rest. Keys: ←/→ previous / next frame, Del removes the selected
point, O toggles "occluded" (v=1), X marks the frame "no pitch" (skipped). Every change is saved to ``--out``.

Labels are ours (proprietary training data): never pre-fill them from a research-only model (PnLCalib, SoccerNet).
Stdlib HTTP server + cv_core; frames stay on this machine.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from convert import FORMAT, video_time, write_labels  # noqa: E402

from cv_core.pitch import FIFA_105x68, KEYPOINT_NAMES, fit_homography, project  # noqa: E402

TPL = FIFA_105x68
LINES = [ln.points.tolist() for ln in TPL.lines(1.0) if ln.kind != "spot"]


class Store:
    def __init__(self, frames_dir: Path, out: Path) -> None:
        self.frames_dir, self.out, self.lock = frames_dir, out, threading.Lock()
        self.images = sorted(p.name for p in frames_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        data = json.loads(out.read_text()) if out.exists() else {"frames": []}
        self.rows: dict[str, dict[str, Any]] = {r["image"]: r for r in data.get("frames", []) if r.get("image")}
        self.skipped: set[str] = set(data.get("skipped", []))

    def save_frame(self, name: str, body: dict[str, Any]) -> None:
        with self.lock:
            self.skipped.discard(name)
            self.rows.pop(name, None)
            if body.get("skip"):
                self.skipped.add(name)
            elif body.get("keypoints"):
                h, w = cv2.imread(str(self.frames_dir / name)).shape[:2]
                video, t_ms = video_time(name)
                self.rows[name] = {"video": video, "t_ms": t_ms, "width": w, "height": h, "image": name,
                                   "keypoints": [[int(i), round(float(x), 1), round(float(y), 1), int(v)]
                                                 for i, x, y, v in body["keypoints"]]}  # fmt: skip
            self.out.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.out.with_suffix(".tmp")
            write_labels(tmp, [self.rows[k] for k in sorted(self.rows)], skipped=sorted(self.skipped),
                         tool="training/keypoints/label_tool.py")  # fmt: skip
            tmp.replace(self.out)


def fit(points: list[list[float]]) -> dict[str, Any]:
    """[[idx, x, y], ...] → projected pitch lines + per-point error (px)."""
    if len(points) < 4:
        return {"ok": False}
    idx = [int(p[0]) for p in points]
    src = TPL.keypoints[idx].astype(float)
    dst = np.array([[p[1], p[2]] for p in points], float)
    try:
        H, _ = fit_homography(src, dst, None, refine=True)
    except Exception as e:  # degenerate (collinear) clicks
        return {"ok": False, "error": str(e)}
    err = np.linalg.norm(project(H, src) - dst, axis=1)
    lines = [project(H, np.array(pts, float)).round(1).tolist() for pts in LINES]
    kps = project(H, TPL.keypoints.astype(float)).round(1).tolist()
    return {"ok": True, "rmse": float(np.sqrt(np.mean(err**2))), "err": err.round(1).tolist(), "lines": lines,
            "keypoints": kps}


def handler(store: Store) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def _json(self, obj: Any, code: int = 200) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            path = unquote(self.path.split("?")[0])
            if path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/state":
                tpl = {"keypoints": TPL.keypoints.tolist(), "names": list(KEYPOINT_NAMES), "lines": LINES,
                       "size": [TPL.length, TPL.width]}  # fmt: skip
                self._json({"template": tpl, "images": store.images, "skipped": sorted(store.skipped),
                            "labels": {k: v["keypoints"] for k, v in store.rows.items()}, "format": FORMAT})
            elif path.startswith("/frames/"):
                p = (store.frames_dir / path.removeprefix("/frames/")).resolve()
                if p.parent != store.frames_dir.resolve() or not p.is_file():
                    return self._json({"error": "not found"}, 404)
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(p.name)[0] or "image/jpeg")
                self.end_headers()
                self.wfile.write(p.read_bytes())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            path = unquote(self.path)
            if path == "/api/fit":
                self._json(fit(body.get("points", [])))
            elif path.startswith("/api/labels/"):
                name = path.removeprefix("/api/labels/")
                if name not in store.images:
                    return self._json({"error": "unknown frame"}, 404)
                store.save_frame(name, body)
                self._json({"saved": True, "labelled": len(store.rows)})
            else:
                self._json({"error": "not found"}, 404)

    return H


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Pitch keypoints</title><style>
body{margin:0;font:14px system-ui;background:#111;color:#ddd;display:grid;grid-template-columns:1fr 440px;height:100vh}
#left{position:relative;overflow:auto}#right{padding:12px;overflow:auto;border-left:1px solid #333}
canvas{display:block;cursor:crosshair}svg{background:#1d5c2e;border-radius:6px}
.kp{cursor:pointer}.sel{stroke:#ff0;stroke-width:3}.done{fill:#4ade80}.todo{fill:#fff}
button{background:#333;color:#ddd;border:1px solid #555;border-radius:4px;padding:4px 8px;margin:2px}
.warn{color:#f87171}.ok{color:#4ade80}li{cursor:pointer}li.cur{color:#ff0}</style></head><body>
<div id="left"><canvas id="c"></canvas></div><div id="right">
<div><b id="title"></b> <span id="count"></span></div>
<div><button id="prev">← Prev</button><button id="next">Next →</button><button id="skip">X No pitch</button>
<button id="nextTodo">Next unlabelled</button></div>
<p>1) click a landmark below · 2) click it on the frame. Del = remove, O = occluded.</p>
<svg id="pitch" width="420" height="272"></svg><div id="fit"></div><ol id="list"></ol>
<p style="color:#888">Never pre-fill from research-only models. Output: <code>pv-pitch-keypoints/v1</code></p></div>
<script>
let S, i = 0, pts = {}, sel = null, overlay = null, img = new Image(), scale = 1;
const c = document.getElementById('c'), g = c.getContext('2d'), svg = document.getElementById('pitch');
const K = 4; // px per metre on the diagram
async function api(u, b){const r=await fetch(u,b?{method:'POST',body:JSON.stringify(b)}:{});return r.json();}
function name(){return S.images[i];}
function load(){const n=name();pts={};(S.labels[n]||[]).forEach(([k,x,y,v])=>pts[k]=[x,y,v]);sel=null;overlay=null;
 img.onload=()=>{scale=Math.min(1,(innerWidth-460)/img.width);c.width=img.width*scale;c.height=img.height*scale;refit();};
 img.src='/frames/'+encodeURIComponent(n);document.getElementById('title').textContent=n;
 const done=Object.keys(S.labels).length;document.getElementById('count').textContent=`${i+1}/${S.images.length} · labelled ${done}`+(S.skipped.includes(n)?' · NO PITCH':'');}
function drawPitch(){svg.innerHTML='';const ns='http://www.w3.org/2000/svg';
 S.template.lines.forEach(l=>{const p=document.createElementNS(ns,'polyline');p.setAttribute('points',l.map(([x,y])=>`${x*K+5},${y*K+5}`).join(' '));p.setAttribute('fill','none');p.setAttribute('stroke','#fff');p.setAttribute('stroke-opacity','.6');svg.appendChild(p);});
 S.template.keypoints.forEach(([x,y],k)=>{const e=document.createElementNS(ns,'circle');e.setAttribute('cx',x*K+5);e.setAttribute('cy',y*K+5);e.setAttribute('r',6);
  e.setAttribute('class','kp '+(k in pts?'done':'todo')+(k===sel?' sel':''));e.onclick=()=>{sel=k;render();};
  const t=document.createElementNS(ns,'title');t.textContent=k+' '+S.template.names[k];e.appendChild(t);svg.appendChild(e);});}
function render(){g.drawImage(img,0,0,c.width,c.height);
 if(overlay&&overlay.ok){g.strokeStyle='rgba(255,0,255,.8)';g.lineWidth=1.5;overlay.lines.forEach(l=>{g.beginPath();l.forEach(([x,y],j)=>j?g.lineTo(x*scale,y*scale):g.moveTo(x*scale,y*scale));g.stroke();});}
 Object.entries(pts).forEach(([k,[x,y,v]])=>{g.fillStyle=+k===sel?'#ff0':(v===1?'#fb923c':'#4ade80');g.beginPath();g.arc(x*scale,y*scale,5,0,7);g.fill();g.fillStyle='#fff';g.fillText(k,x*scale+7,y*scale-7);});
 drawPitch();const list=document.getElementById('list');list.innerHTML='';
 Object.keys(pts).sort((a,b)=>a-b).forEach(k=>{const li=document.createElement('li');li.textContent=`${k} ${S.template.names[k]}${pts[k][2]===1?' (occluded)':''}`+(overlay&&overlay.err?'':'');if(+k===sel)li.className='cur';li.onclick=()=>{sel=+k;render();};list.appendChild(li);});
 const f=document.getElementById('fit');f.innerHTML=!overlay?'':overlay.ok?`<span class="${overlay.rmse>6?'warn':'ok'}">fit RMSE ${overlay.rmse.toFixed(1)} px</span> (${Object.keys(pts).length} points; aim ≥ 6, RMSE ≤ 6)`:`<span class="warn">no fit ${overlay.error||''}</span>`;}
async function refit(){const p=Object.entries(pts).map(([k,[x,y]])=>[+k,x,y]);overlay=p.length>=4?await api('/api/fit',{points:p}):null;render();}
async function save(skip){const n=name();const kp=Object.entries(pts).map(([k,[x,y,v]])=>[+k,x,y,v]);const r=await api('/api/labels/'+encodeURIComponent(n),skip?{skip:true}:{keypoints:kp});
 if(skip){S.skipped.push(n);delete S.labels[n];}else{S.skipped=S.skipped.filter(s=>s!==n);if(kp.length)S.labels[n]=kp;else delete S.labels[n];}}
c.onclick=async e=>{if(sel===null)return;const r=c.getBoundingClientRect();pts[sel]=[(e.clientX-r.left)/scale,(e.clientY-r.top)/scale,2];
 const order=S.template.keypoints.map((_,k)=>k).filter(k=>!(k in pts));await save();sel=null;refit();};
onkeydown=async e=>{if(e.key==='ArrowRight')go(1);if(e.key==='ArrowLeft')go(-1);
 if((e.key==='Delete'||e.key==='Backspace')&&sel in pts){delete pts[sel];await save();refit();}
 if(e.key.toLowerCase()==='o'&&sel in pts){pts[sel][2]=pts[sel][2]===1?2:1;await save();render();}
 if(e.key.toLowerCase()==='x'){await save(true);go(1);}};
function go(d){i=Math.max(0,Math.min(S.images.length-1,i+d));load();}
document.getElementById('prev').onclick=()=>go(-1);document.getElementById('next').onclick=()=>go(1);
document.getElementById('skip').onclick=async()=>{await save(true);go(1);};
document.getElementById('nextTodo').onclick=()=>{for(let j=i+1;j<S.images.length;j++){const n=S.images[j];if(!(n in S.labels)&&!S.skipped.includes(n)){i=j;return load();}}};
api('/api/state').then(s=>{S=s;svg.setAttribute('width',s.template.size[0]*K+10);svg.setAttribute('height',s.template.size[1]*K+10);load();});
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    store = Store(a.frames.resolve(), a.out.resolve())
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), handler(store))
    print(f"{len(store.images)} frames, {len(store.rows)} labelled → http://127.0.0.1:{a.port}  (Ctrl-C to stop)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
