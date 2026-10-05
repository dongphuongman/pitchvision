"""Render docs/status.json into the STATUS block of docs/PLAN.html."""
import html
import json
import re
from pathlib import Path

DOCS = Path(__file__).parent
BADGE = {
    "done": ("Xong", "var(--accent)"),
    "partial": ("Một phần", "var(--warn)"),
    "in_progress": ("Đang làm", "var(--accent-2)"),
    "blocked": ("Bị chặn", "var(--danger)"),
    "fail": ("Không đạt", "var(--danger)"),
    "pending": ("Chưa làm", "var(--fg-3)"),
}


def badge(state: str) -> str:
    label, color = BADGE[state]
    return (f'<span class="pill" style="color:{color};border-color:color-mix(in srgb,{color} 45%,transparent)">'
            f"{label}</span>")


def render(data: dict) -> str:
    counts = {k: sum(1 for i in data["items"] if i["state"] == k) for k in BADGE}
    rows = "\n".join(
        f'      <tr><td class="id">{i["id"]}</td><td>{html.escape(i["title"])}</td>'
        f'<td>{badge(i["state"])}</td><td>{i["note"]}</td></tr>'
        for i in data["items"]
    )
    log = "\n".join(
        f'      <li><code>{e["date"]}</code> {e["text"]}</li>' for e in reversed(data["log"])
    )
    summary = " · ".join(f"{BADGE[k][0]}: <strong>{v}</strong>" for k, v in counts.items() if v)
    return f"""<!-- STATUS:BEGIN -->
<section id="status">
  <h2><span class="n">▶</span>Trạng thái thực hiện</h2>
  <p>Cập nhật: <code>{data["updated"]}</code> · {summary}. Nguồn: <code>docs/status.json</code> → <code>python3 docs/update_status.py</code>.</p>
  <table>
    <thead><tr><th>ID</th><th>Hạng mục</th><th>Trạng thái</th><th>Ghi chú</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
  <h3>Nhật ký</h3>
  <ul>
{log}
  </ul>
</section>
<!-- STATUS:END -->"""


def main() -> None:
    data = json.loads((DOCS / "status.json").read_text(encoding="utf-8"))
    plan = DOCS / "PLAN.html"
    s = plan.read_text(encoding="utf-8")
    block = render(data)
    if "<!-- STATUS:BEGIN -->" in s:
        s = re.sub(r"<!-- STATUS:BEGIN -->.*?<!-- STATUS:END -->", lambda _: block, s, flags=re.S)
    else:
        s = s.replace('<!-- ============ 0 ============ -->', block + "\n\n<!-- ============ 0 ============ -->", 1)
        s = s.replace('<a href="#s0">', '<a href="#status">▶ Trạng thái thực hiện</a>\n  <a href="#s0">', 1)
    plan.write_text(s, encoding="utf-8")
    print("PLAN.html status updated:", {k: v for k, v in
          {k: sum(1 for i in data["items"] if i["state"] == k) for k in BADGE}.items() if v})


if __name__ == "__main__":
    main()
