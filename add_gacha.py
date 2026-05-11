#!/usr/bin/env python3
"""加新子類別「扭蛋」+ 3 家精選 row + 既有 row 兼類別 / 備註

- 3 家用 API 抓：ガシャポンのデパート / C-pla / ガチャゲーミックス
- ヨドバシ 已在 csv：兼類別 += 扭蛋
- 中川政七 已在 csv：寫備註到 shopping_notes.txt（不改類別/兼類別）

Usage:
    python add_gacha.py --yes        # 跑（會打 3 次 API ≈ $0.10）
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 重用 refresh_shopping 內的 helpers（API 設定、normalize、find_row、place_to_row）
from refresh_shopping import (
    SHOPPING_CSV, SHOPPING_HEADERS,
    text_search_one, place_to_row, find_row, get_cid,
)

BASE = Path(__file__).resolve().parent
NOTES_PATH = BASE / "cleaned" / "shopping_notes.txt"
DATA_JS = BASE / "data.js"

# 3 家扭蛋精選用 API 抓
GACHA_QUERIES = [
    "ガシャポンのデパート イオンモールKYOTO",
    "C-pla 京都寺町京極",
    "ガチャゲーミックス 京都アバンティ",
]

# 既有 row 的「兼類別」加上「扭蛋」
ALSO_GACHA_SUBSTR = [
    "ヨドバシカメラ マルチメディア京都",
]

# 既有 row 的備註（寫到 shopping_notes.txt）
EXTRA_NOTES = [
    ("中川政七商店", "有海洋堂 × 中川政七聯名扭蛋"),
]


def add_to_extra(row: dict, cat: str) -> bool:
    """把 cat 加到 row['兼類別']（; 分隔），已存在則 noop。回傳是否新加。"""
    existing = [e for e in (row.get("兼類別") or "").split(";") if e]
    if cat in existing:
        return False
    existing.append(cat)
    row["兼類別"] = ";".join(existing)
    return True


def main() -> None:
    if not (("--yes" in sys.argv) or ("-y" in sys.argv)):
        print("需要 --yes 才會跑（會打 3 次 API ≈ $0.10）")
        return

    with open(SHOPPING_CSV, encoding="utf-8-sig") as f:
        rows: list[dict] = list(csv.DictReader(f))
    for r in rows:
        r.setdefault("精選", "")
        r.setdefault("兼類別", "")
    print(f"📂 載入 {len(rows)} 列")

    # ============================================================
    # Step 1: 抓 3 家扭蛋精選
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 1：抓 3 家扭蛋精選")
    print("=" * 60)
    for q in GACHA_QUERIES:
        try:
            place = text_search_one(q)
        except Exception as e:
            print(f"  ❌ 「{q}」失敗：{e}")
            continue
        if not place:
            print(f"  ❌ 「{q}」找不到京都府結果")
            continue
        cid = get_cid(place.get("googleMapsUri", ""))
        # 同 cid 已存在？→ 改為兼類別 += 扭蛋
        existing_idx = None
        for i, r in enumerate(rows):
            if cid and get_cid(r.get("Google Maps連結", "")) == cid:
                existing_idx = i; break
        if existing_idx is not None:
            r = rows[existing_idx]
            added = add_to_extra(r, "扭蛋")
            print(f"  · 「{q}」cid 已存在：「{r['日文店名']}」({r['類別']})；"
                  f"{'加' if added else '已有'} 兼類別=扭蛋")
        else:
            new_row = place_to_row(place, "扭蛋")
            new_row["精選"] = "TRUE"
            rows.append(new_row)
            name = new_row["日文店名"]
            print(f"  ✓ 新增「{name}」⭐{new_row['評分']}（{new_row['評論數']}）")
        time.sleep(0.3)

    # ============================================================
    # Step 2: 既有 row 兼類別 += 扭蛋
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 2：既有 row 兼類別 += 扭蛋")
    print("=" * 60)
    for substr in ALSO_GACHA_SUBSTR:
        m = find_row(rows, substr)
        if m is None:
            print(f"  ⚠ 找不到「{substr}」")
            continue
        idx, r = m
        before = r.get("兼類別") or "(空)"
        add_to_extra(r, "扭蛋")
        print(f"  ✓ {r['日文店名']}：兼類別 {before} → {r.get('兼類別')}")

    # ============================================================
    # Step 3: 備註寫入 shopping_notes.txt
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 3：寫備註到 shopping_notes.txt")
    print("=" * 60)
    notes_existing = NOTES_PATH.read_text(encoding="utf-8") if NOTES_PATH.exists() else ""
    additions: list[str] = []
    for substr, note in EXTRA_NOTES:
        m = find_row(rows, substr)
        if m is None:
            print(f"  ⚠ 找不到「{substr}」")
            continue
        idx, r = m
        cid = get_cid(r.get("Google Maps連結", ""))
        line = f"[{r['類別']}] {r['日文店名']}（cid={cid}）：{note}"
        if line in notes_existing:
            print(f"  · 已存在：{line}")
        else:
            additions.append(line)
            print(f"  ✓ {line}")
    if additions:
        with open(NOTES_PATH, "a", encoding="utf-8") as f:
            for ln in additions:
                f.write(ln + "\n")

    # ============================================================
    # Step 4: 寫回 shopping.csv + data.js
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 4：寫回 shopping.csv + data.js")
    print("=" * 60)
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
    new_block = "window.__SHOPPING_DATA = " + json.dumps(csv_text, ensure_ascii=False) + ";\n"
    DATA_JS.write_text(existing + new_block, encoding="utf-8")
    print(f"  📁 {DATA_JS}")


if __name__ == "__main__":
    main()
