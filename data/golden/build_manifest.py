"""Build manifest.json + manifest.csv for the broadcast golden set from segments.txt and ffprobe."""
import csv
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in v["avg_frame_rate"].split("/"))
    return {
        "width": v["width"],
        "height": v["height"],
        "fps": round(num / den, 3) if den else None,
        "codec": v["codec_name"],
        "duration_s": round(float(data["format"]["duration"]), 1),
        "size_mb": round(int(data["format"]["size"]) / 1e6, 1),
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }


def main() -> None:
    rows = []
    for line in (ROOT / "segments.txt").read_text(encoding="utf-8").splitlines():
        vid, category, match, channel, start = line.split("|")
        m, s = (int(x) for x in start.split(":"))
        start_s = m * 60 + s
        path = ROOT / "raw" / f"{vid}.mp4"
        row = {
            "id": vid,
            "category": category,
            "match": match,
            "channel": channel,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "segment_start_s": start_s,
            "segment_end_s": start_s + 900,
            "file": f"raw/{vid}.mp4",
            "downloaded": path.exists(),
        }
        if path.exists():
            row.update(probe(path))
        rows.append(row)

    (ROOT / "manifest.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(ROOT / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["downloaded"]]
    print(f"{len(ok)}/{len(rows)} downloaded, {sum(r.get('size_mb', 0) for r in ok) / 1000:.1f} GB")
    for r in rows:
        if r["downloaded"]:
            print(f"  {r['id']:12} {r['category']:6} {r['width']}x{r['height']} {r['fps']:>6}fps {r['duration_s']:>6}s {r['size_mb']:>7}MB  {r['match'][:48]}")
        else:
            print(f"  {r['id']:12} MISSING  {r['match'][:48]}")


if __name__ == "__main__":
    main()
