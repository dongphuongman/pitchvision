"""SP-08 — run the overlay-sync page in Chrome (headless, throwaway profile) and save the result JSON."""
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
OUT = HERE / "results" / "sp08"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    srv = subprocess.Popen([sys.executable, "serve.py", "8766"], cwd=HERE / "sp08_overlay")
    time.sleep(1)
    try:
        with sync_playwright() as p:
            for label, args in [("chrome_gpu", []), ("chrome_swdecode", ["--disable-accelerated-video-decode"])]:
                b = p.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required", *args])
                page = b.new_page(viewport={"width": 1280, "height": 900})
                page.goto("http://127.0.0.1:8766/#auto")
                page.wait_for_function("window.__sp08", timeout=120_000)
                res = page.evaluate("window.__sp08")
                page.screenshot(path=str(OUT / f"{label}.png"))
                (OUT / f"{label}.json").write_text(json.dumps(res, indent=1))
                print(label, json.dumps({k: res[k] for k in ("missedCallbacksDuringPlay", "seekTargetShown")}))
                for ph, ms in res["stats"].items():
                    for m, s in ms.items():
                        print(f"  {ph:12s} {m:18s} n={s['n']:4d} exact={s['exact']/s['n']:.3f} ±1={s['within1']/s['n']:.3f} max={s['maxAbs']} hist={s['hist']}")
                b.close()
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()
