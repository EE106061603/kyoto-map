#!/usr/bin/env python3
"""補「大垣書店 烏丸御池本店」+ 把書店子類別併入文具

Usage:
    python patch_books.py --yes      # 跑（會打 1 次 API ≈ $0.03）
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from refresh_shopping import (
    SHOPPING_CSV, SHOPPING_HEADERS,
    text_search_one, place_to_row, get_cid,
)

BASE = Path(__file__).resolve().parent
DATA_JS = BASE / "data.js"
QUERY = "大垣書店 烏丸御池本店"


def main() -> None:
    if not (("--yes" in sys.argv) or ("-y" in sys.argv)):
        print("--yes 才會跑（會打 1 次 API ≈ $0.03）")
        return

    with open(SHOPPING_CSV, encoding="utf-8-sig") as f:
        rows: list[dict] = list(csv.DictReader(f))
    for r in rows:
        r.setdefault("精選", "")
        r.setdefault("兼類別", "")
    print(f"📂 載入 {len(rows)} 列")

    # ───── Step 1: 抓大垣書店 烏丸御池本店 ─────
    print(f"\n=== Step 1: 抓「{QUERY}」===")
    try:
        place = text_search_one(QUERY)
    except Exception as e:
        print(f"  ❌ API 失敗：{e}")
        return
    if not place:
        print("  ❌ 找不到京都府結果")
    else:
        cid = get_cid(place.get("googleMapsUri", ""))
        existing = next(
            (r for r in rows if cid and get_cid(r.get("Google Maps連結", "")) == cid),
            None,
        )
        if existing:
            old_cat = existing["類別"]
            existing["類別"] = "文具"
            existing["精選"] = "TRUE"
            print(f"  · 已存在 cid={cid}：「{existing['日文店名']}」({old_cat} → 文具，精選=TRUE)")
        else:
            new_row = place_to_row(place, "文具")
            new_row["精選"] = "TRUE"
            rows.append(new_row)
            print(f"  ✓ 新增「{new_row['日文店名']}」⭐{new_row['評分']}（{new_row['評論數']}）→ 文具（精選）")

    # ───── Step 2: 書店 → 文具 ─────
    print("\n=== Step 2: 書店類別合併到文具 ===")
    merged = 0
    for r in rows:
        if r["類別"] == "書店":
            r["類別"] = "文具"
            merged += 1
            print(f"  ✓ {r['日文店名']}：書店 → 文具")
    print(f"  共合併 {merged} 筆")

    # ───── Step 3: 寫回 ─────
    print("\n=== Step 3: 寫回 shopping.csv + data.js ===")
    with open(SHOPPING_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHOPPING_HEADERS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in SHOPPING_HEADERS})
    print(f"  📁 {SHOPPING_CSV}（{len(rows)} 列）")

    buf = io.StringIO()
    w2 = csv.DictWriter(buf, fieldnames=SHOPPING_HEADERS)
    w2.writeheader()
    for r in rows:
        w2.writerow({k: r.get(k, "") for k in SHOPPING_HEADERS})
    csv_text = buf.getvalue()
    existing = DATA_JS.read_text(encoding="utf-8") if DATA_JS.exists() else ""
    existing = re.sub(r"window\.__SHOPPING_DATA\s*=\s*.*?;\s*\n", "", existing, flags=re.S)
    DATA_JS.write_text(
        existing + "window.__SHOPPING_DATA = "
        + json.dumps(csv_text, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    print(f"  📁 {DATA_JS}")


if __name__ == "__main__":
    main()
