"""購物資料報告（讀 cleaned/shopping.csv 與 shopping_notes.txt）→ shopping_summary.md"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
SRC = BASE / "cleaned" / "shopping.csv"
NOTES = BASE / "cleaned" / "shopping_notes.txt"
OUT = BASE / "shopping_summary.md"

CAT_ORDER = ["職人專門", "選物生活", "文具", "百貨", "超市", "扭蛋"]
CAT_ICON = {
    "職人專門": "🏺", "選物生活": "🛍️", "文具": "✏️",
    "百貨": "🏬", "超市": "🛒", "扭蛋": "🎰",
}


def load() -> list[dict]:
    with open(SRC, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main() -> None:
    rows = load()
    print(f"載入 {len(rows)} 列")
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["類別"], []).append(r)

    md: list[str] = []
    md.append("# 京都購物資料整理\n")
    md.append("資料來源：Google Places API (New) Text Search、收集日 2026-05-10\n")
    total_curated = sum(1 for r in rows if r.get("精選") == "TRUE")
    total_cross = sum(1 for r in rows if r.get("兼類別"))
    md.append(f"**總筆數：{len(rows)}**　|　精選 {total_curated} 筆　|　跨類別重複 {total_cross} 筆（兼類別欄填入餐廳分類）\n")

    # 各子類別計數摘要
    md.append("## 子類別摘要\n")
    md.append("| 子類別 | 全部 | 精選 |")
    md.append("|--------|------|------|")
    for cat in CAT_ORDER:
        rs = by_cat.get(cat, [])
        cur = sum(1 for r in rs if r.get("精選") == "TRUE")
        ico = CAT_ICON.get(cat, "")
        md.append(f"| {ico} {cat} | {len(rs)} | {cur} |")
    md.append("")

    # 精選清單（按子類別）
    md.append("## 精選名單對照\n")
    md.append("（精選=TRUE 的 row，按子類別分組）\n")
    for cat in CAT_ORDER:
        cur_rows = [r for r in by_cat.get(cat, []) if r.get("精選") == "TRUE"]
        if not cur_rows:
            continue
        ico = CAT_ICON.get(cat, "")
        md.append(f"### {ico} {cat}（{len(cur_rows)} 間）\n")
        cur_rows.sort(key=lambda r: int(r.get("評論數") or 0), reverse=True)
        for r in cur_rows:
            name = r["日文店名"]
            rating = r.get("評分") or "-"
            reviews = r.get("評論數") or "0"
            cross = r.get("兼類別") or ""
            cross_str = f"｜兼 {cross}" if cross else ""
            md.append(f"- ⭐ **{name}**　⭐{rating}（{reviews}）{cross_str}")
        md.append("")

    # 完整清單（按子類別、評論降冪）
    md.append("## 各子類別完整清單\n")
    for cat in CAT_ORDER:
        cat_rows = by_cat.get(cat, [])
        if not cat_rows:
            continue
        ico = CAT_ICON.get(cat, "")
        md.append(f"### {ico} {cat}（{len(cat_rows)} 間）\n")
        cat_rows = sorted(cat_rows, key=lambda r: int(r.get("評論數") or 0), reverse=True)
        md.append("| # | 精選 | 店名 | 評分 | 評論 | 兼類別 | 地址 | Google Maps |")
        md.append("|---|------|------|------|------|--------|------|-------------|")
        for i, r in enumerate(cat_rows, 1):
            sel = "⭐" if r.get("精選") == "TRUE" else ""
            name = r.get("日文店名") or r.get("店名") or ""
            rating = r.get("評分") or "-"
            reviews = r.get("評論數") or "0"
            cross = r.get("兼類別") or ""
            addr = (r.get("地址") or "").replace("|", "\\|")
            url = r.get("Google Maps連結") or ""
            link = f"[連結]({url})" if url else ""
            md.append(f"| {i} | {sel} | {name} | {rating} | {reviews} | {cross} | {addr} | {link} |")
        md.append("")

    # 需人工確認註記（從 shopping_notes.txt 讀）
    if NOTES.exists():
        notes = NOTES.read_text(encoding="utf-8").strip()
        if notes:
            md.append("## 需人工確認\n")
            md.append("```\n" + notes + "\n```\n")

    OUT.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"📁 已寫 {OUT}（{len(md)} 行）")


if __name__ == "__main__":
    main()
