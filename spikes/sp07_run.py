"""SP-07 — drive the upload page in headless Chrome against a local S3 (SeaweedFS) and verify bytes.

    docker run -d --rm --name pv-s3 -p 8333:8333 -v $PWD/sp07_upload/s3.json:/etc/s3.json:ro \
        chrislusf/seaweedfs server -s3 -s3.config=/etc/s3.json -dir=/data
    python sp07_run.py
"""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "sp07_upload"))
from server import BUCKET, s3  # noqa: E402

OUT = HERE / "results" / "sp07"
FILE = HERE.parent / "data" / "golden" / "raw" / "BukNW8GRI5A.mp4"


def md5_file(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(8 << 20), b""):
            h.update(c)
    return h.hexdigest()


def md5_object(key: str) -> str:
    h = hashlib.md5()
    for c in s3.get_object(Bucket=BUCKET, Key=key)["Body"].iter_chunks(8 << 20):
        h.update(c)
    return h.hexdigest()


def run(page, query: str, reload: bool = False) -> dict:
    if not reload:
        page.goto(f"http://127.0.0.1:8767/?{query}")
    page.set_input_files("#file", str(FILE))
    page.wait_for_function("window.__sp07", timeout=600_000)
    return page.evaluate("window.__sp07")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    srv = subprocess.Popen([sys.executable, "server.py", "8767"], cwd=HERE / "sp07_upload")
    time.sleep(2)
    local = md5_file(FILE)
    results = {"file": FILE.name, "size": FILE.stat().st_size, "scenarios": {}}
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(channel="chrome", headless=True)
            page = b.new_page()
            for name, q in [("clean_c4_16MiB", "conc=4&part=16"), ("clean_c8_16MiB", "conc=8&part=16"),
                            ("faults_20pct", "conc=4&part=16&fail=0.2")]:
                r = run(page, q)
                r["md5_ok"] = r.get("status") == "complete" and md5_object(r["key"]) == local
                results["scenarios"][name] = r
                print(name, json.dumps(r))
            r1 = run(page, "conc=4&part=16&stopAfter=8")            # "tab closed" after 8 parts
            page.goto("http://127.0.0.1:8767/?conc=4&part=16")       # reopen, same file → resume
            r2 = run(page, "", reload=True)
            r2["md5_ok"] = r2.get("status") == "complete" and md5_object(r2["key"]) == local
            results["scenarios"]["resume_after_interrupt"] = {"first": r1, "second": r2}
            print("resume", json.dumps(r1), json.dumps(r2))
            b.close()
    finally:
        srv.terminate()
    keys = [r["key"] for r in results["scenarios"].values() for r in ([r["second"]] if "second" in r else [r]) if r.get("key")]
    for k in keys:  # spike objects are throwaway
        s3.delete_object(Bucket=BUCKET, Key=k)
    results["endpoint"] = s3.meta.endpoint_url.split("//")[1].split(".")[-3] if "r2." in s3.meta.endpoint_url else s3.meta.endpoint_url
    results["deleted_objects"] = len(keys)
    name = "sp07_results_r2.json" if "r2.cloudflarestorage" in s3.meta.endpoint_url else "sp07_results.json"
    (OUT / name).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
