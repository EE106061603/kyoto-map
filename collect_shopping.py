#!/usr/bin/env python3
"""京都購物資料收集腳本

兩種搜尋方式併行：

- 方式 A（精選名單）：對每個指定店名打 Text Search（query = "店名 京都"），
  取第一筆地址含「京都府」的結果。

- 方式 B（關鍵字補滿）：用日文關鍵字 Text Search（每組 query 翻 3 頁、最多 60 筆），
  過濾「京都府」+ 評分/評論門檻，dedup 掉方式 A 已抓到的店，
  按評論數 desc 排序，每子類別補到 30 間（不含已 dedup 的精選）。

5 子類別（大類別：購物）：
    職人專門 / 選物生活 / 文具 / 百貨 / 超市

輸出：
    cleaned/shopping.csv      欄位與 cleaned/attractions.csv 一致
    data.js                   追加 window.__SHOPPING_DATA（供 map.html file:// 雙擊讀）

Usage:
    python collect_shopping.py --dry-run    # 印 API 預估，不打 API
    python collect_shopping.py --yes        # 直接跑（跳過確認）
    python collect_shopping.py              # 跑（會問確認）
    python collect_shopping.py --only=文具,超市
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Windows utf-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================
# 設定
# ============================================================
load_dotenv()
API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
if not API_KEY:
    sys.exit("錯誤：找不到 GOOGLE_MAPS_API_KEY，請檢查 .env")

KYOTO_STATION = {"latitude": 34.985849, "longitude": 135.758767}
SEARCH_RADIUS_M = 10_000
KYOTO_BIAS = {"circle": {"center": KYOTO_STATION, "radius": SEARCH_RADIUS_M}}

# 每子類別目標筆數（精選 + 補滿）
TARGET_PER_CAT = 30

# 方式 B 品質門檻（精選不過濾）
MIN_RATING_B = 3.5
MIN_REVIEWS_B = 30

# 5 個子類別（query 用日文，curated 用使用者提供的精選清單）
# 中文 / Chinese 字「站」normalize 成日文「駅」用於查詢，但保留原拼寫供顯示
SHOPPING_CATS: dict[str, dict] = {
    "職人專門": {
        "queries": ["京都 老舗 専門店", "京都 職人 工芸"],
        "curated": [
            "市原平兵衛商店",          # 筷子
            "永楽屋 細辻伊兵衛商店",   # 手拭巾
            "開化堂",                  # 銅茶筒
            "象彥",                    # 漆器
            "松榮堂",                  # 線香
            "山田松香木店",            # 香木
            "鳩居堂 京都本店",         # 和風文具
            "有次",                    # 廚刀（錦市場內）
            "唐草屋",                  # 風呂敷
            "むす美 京都店",           # 風呂敷
            "一澤信三郎帆布",          # 帆布包
            "朝日堂",                  # 清水寺旁陶器
            "宮脇賣扇庵",              # 扇子
            "白竹堂 京都本店",         # 扇子
        ],
    },
    "選物生活": {
        "queries": ["京都 セレクトショップ", "京都 雑貨店"],
        "curated": [
            "D&Department KYOTO",
            "中川政七商店 京都本店",
            "SOU・SOU 本店",
            "SOU・SOU 足袋",
            "SOU・SOU 布袋",
            "Angers Kyoto",
            "graf 京都",
            "Pass The Baton 京都祇園店",
            "mina perhonen 京都",
            "45R 京都店",
        ],
    },
    "文具": {
        "queries": ["京都 文房具", "京都 ノート"],
        "curated": [
            "TRAVELER'S FACTORY KYOTO",
            "裏具",
            "嵩山堂はし本",
            "Smith 京都 Porta",
        ],
    },
    "百貨": {
        "queries": ["京都 百貨店", "京都 ショッピングモール"],
        "curated": [
            "京都伊勢丹",
            "髙島屋 京都店",
            "大丸 京都店",
            "京都 BAL",
            "新風館",
            "京都駅 The Cube",
            "京都駅 Porta",
        ],
    },
    "超市": {
        "queries": ["京都 スーパー", "京都 スーパーマーケット"],
        "curated": [],
    },
}

# Endpoint
TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Text Search 欄位（與 collect.py 對齊）
TEXT_SEARCH_FIELDS = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.rating",
    "places.userRatingCount",
    "places.priceLevel",
    "places.regularOpeningHours",
    "places.googleMapsUri",
    "places.websiteUri",
    "places.photos",
    "nextPageToken",
])

PRICE_LEVEL_MAP = {
    "PRICE_LEVEL_FREE": "免費",
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
}

CSV_HEADERS = [
    "類別", "店名", "日文店名", "地址", "緯度", "經度",
    "直線距離（km）",
    "評分", "評論數", "價位等級", "營業時間",
    "交通時間（分鐘）", "步行時間（分鐘）", "換乘次數",
    "Google Maps連結", "店家網站", "照片資源",
    # shopping.csv 專屬：refresh_shopping.py 寫入 / 維護
    "精選", "兼類別",
]

_TEXT_CALLS = 0


# ============================================================
# 工具
# ============================================================
def haversine_km(lat1, lng1, lat2, lng2):
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
           * math.sin(dlng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def normalize_query(name: str) -> str:
    """Chinese「站」→ 日文「駅」，提升 Google Places 命中率。"""
    return name.replace("站", "駅")


def post_with_retry(url, headers, body, *, label="", max_retry=4):
    delay = 1.0
    last_err = None
    for attempt in range(1, max_retry + 1):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=30)
        except requests.exceptions.RequestException as e:
            last_err = f"連線錯誤：{e}"
            if attempt == max_retry:
                raise
            print(f"  ⚠ {label} {last_err}，{delay:.0f}s 後重試...")
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < max_retry:
            print(f"  ⚠ {label} HTTP {r.status_code}，{delay:.0f}s 後重試...")
            time.sleep(delay)
            delay *= 2
            continue
        raise RuntimeError(f"{label} HTTP {r.status_code}: {r.text[:600]}")
    raise RuntimeError(f"{label} 重試次數用盡：{last_err}")


# ============================================================
# Text Search
# ============================================================
def text_search_paged(query: str, *, max_results: int = 60) -> list[dict]:
    """方式 B 用：翻頁抓最多 60 筆"""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": TEXT_SEARCH_FIELDS,
    }
    body_base = {
        "textQuery": query,
        "languageCode": "ja",
        "regionCode": "JP",
        "locationBias": KYOTO_BIAS,
        "pageSize": 20,
    }
    results: list[dict] = []
    page_token: Optional[str] = None
    page = 0
    while page < 3 and len(results) < max_results:
        body = dict(body_base)
        if page_token:
            body["pageToken"] = page_token
        global _TEXT_CALLS
        _TEXT_CALLS += 1
        data = post_with_retry(
            TEXT_SEARCH_URL, headers, body,
            label=f"TextSearch[{query} p{page+1}]",
        )
        results.extend(data.get("places", []))
        page_token = data.get("nextPageToken")
        page += 1
        if not page_token:
            break
        time.sleep(2.0)
    return results


def text_search_one(query: str) -> Optional[dict]:
    """方式 A 用：單頁取最多 5 筆，回第一筆地址含「京都府」者。"""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": TEXT_SEARCH_FIELDS,
    }
    body = {
        "textQuery": query,
        "languageCode": "ja",
        "regionCode": "JP",
        "locationBias": KYOTO_BIAS,
        "pageSize": 5,
    }
    global _TEXT_CALLS
    _TEXT_CALLS += 1
    data = post_with_retry(
        TEXT_SEARCH_URL, headers, body,
        label=f"NameSearch[{query}]",
    )
    for p in data.get("places") or []:
        if "京都府" in (p.get("formattedAddress") or ""):
            return p
    return None


# ============================================================
# 整理 → CSV row
# ============================================================
def format_opening_hours(opening: Optional[dict]) -> str:
    if not opening:
        return ""
    return " | ".join(opening.get("weekdayDescriptions") or [])


def first_photo_resource(photos: Optional[list]) -> str:
    if not photos:
        return ""
    return photos[0].get("name", "")


def to_row(label: str, p: dict) -> dict:
    loc = p.get("location") or {}
    lat = loc.get("latitude")
    lng = loc.get("longitude")
    name = (p.get("displayName") or {}).get("text", "")
    addr = p.get("formattedAddress", "")
    rating = p.get("rating") or 0
    reviews = p.get("userRatingCount") or 0
    distance_km = (
        round(haversine_km(KYOTO_STATION["latitude"], KYOTO_STATION["longitude"], lat, lng), 2)
        if (lat is not None and lng is not None) else ""
    )
    return {
        "類別": label,
        "店名": name,           # 購物店家通常已多語混用，不再走 translate_name
        "日文店名": name,
        "地址": addr,
        "緯度": lat if lat is not None else "",
        "經度": lng if lng is not None else "",
        "直線距離（km）": distance_km,
        "評分": rating or "",
        "評論數": reviews or "",
        "價位等級": PRICE_LEVEL_MAP.get(p.get("priceLevel", ""), ""),
        "營業時間": format_opening_hours(p.get("regularOpeningHours")),
        "交通時間（分鐘）": "",
        "步行時間（分鐘）": "",
        "換乘次數": "",
        "Google Maps連結": p.get("googleMapsUri", ""),
        "店家網站": p.get("websiteUri", ""),
        "照片資源": first_photo_resource(p.get("photos")),
        "精選": "",
        "兼類別": "",
    }


# ============================================================
# 蒐集流程
# ============================================================
def collect_curated(label: str, names: list[str]) -> list[tuple[str, dict]]:
    """方式 A：精選名單，回 [(place_id, row), ...]"""
    out: list[tuple[str, dict]] = []
    for raw in names:
        q = f"{normalize_query(raw)} 京都"
        try:
            p = text_search_one(q)
        except Exception as e:
            print(f"  ⚠ 「{raw}」查詢失敗：{e}")
            continue
        if not p:
            print(f"  ⚠ 「{raw}」找不到京都的店，略過")
            continue
        pid = p.get("id") or ""
        out.append((pid, to_row(label, p)))
        addr = p.get("formattedAddress", "")[:30]
        rating = p.get("rating") or "-"
        reviews = p.get("userRatingCount") or 0
        print(f"  ✓ 精選 +「{raw}」⭐{rating}（{reviews}）→ {addr}")
        time.sleep(0.3)
    return out


def collect_supplemental(
    label: str,
    queries: list[str],
    excluded_ids: set,
    target_total: int,
    already: int,
) -> list[dict]:
    """方式 B：關鍵字搜尋補到 target_total（已含精選 already 筆）"""
    seen: dict[str, dict] = {}
    for q in queries:
        try:
            places = text_search_paged(q, max_results=60)
        except Exception as e:
            print(f"  ⚠ 「{q}」搜尋失敗：{e}")
            continue
        new = 0
        for p in places:
            pid = p.get("id")
            if pid and pid not in seen and pid not in excluded_ids:
                seen[pid] = p
                new += 1
        print(f"    「{q}」+{new}（補池累計 {len(seen)}）")
        time.sleep(0.3)

    # 排序：評論數 desc → 評分 desc
    candidates = sorted(
        seen.values(),
        key=lambda p: (p.get("userRatingCount") or 0, p.get("rating") or 0),
        reverse=True,
    )

    rows: list[dict] = []
    needed = max(0, target_total - already)
    not_kyoto = quality_skip = no_coord = 0
    for p in candidates:
        if len(rows) >= needed:
            break
        addr = p.get("formattedAddress") or ""
        if "京都府" not in addr:    # 必加：locationBias 是 soft hint，會混東京/埼玉
            not_kyoto += 1
            continue
        rating = p.get("rating") or 0
        reviews = p.get("userRatingCount") or 0
        if rating and rating < MIN_RATING_B:
            quality_skip += 1
            continue
        if reviews < MIN_REVIEWS_B:
            quality_skip += 1
            continue
        loc = p.get("location") or {}
        if loc.get("latitude") is None or loc.get("longitude") is None:
            no_coord += 1
            continue
        rows.append(to_row(label, p))
        excluded_ids.add(p.get("id"))
    print(
        f"    補滿 {len(rows)}/{needed}（候選 {len(candidates)}；"
        f"非京都 {not_kyoto}、品質不足 {quality_skip}、無座標 {no_coord}）"
    )
    return rows


# ============================================================
# 跨類別 dedup（同 cid 多列只保留第一個；無 cid 全保留）
# 子類別在 SHOPPING_CATS 中的順序即優先序：職人專門 > 選物生活 > 文具 > 百貨 > 超市
# 精選因為在補滿前 append，所以同 cid 也會精選贏。
# ============================================================
def dedup_by_cid(rows: list[dict]) -> tuple[list[dict], int]:
    seen: set[str] = set()
    out: list[dict] = []
    dup = 0
    for r in rows:
        m = re.search(r"cid=(\d+)", r.get("Google Maps連結") or "")
        cid = m.group(1) if m else ""
        if cid:
            if cid in seen:
                dup += 1
                continue
            seen.add(cid)
        out.append(r)
    return out, dup


# ============================================================
# data.js 更新（不動既有 __CSV_DATA / __ATTRACTIONS_DATA）
# ============================================================
SHOPPING_BLOCK_PAT = re.compile(
    r"window\.__SHOPPING_DATA\s*=\s*.*?;\s*\n",
    re.S,
)


def update_data_js(base: Path, shopping_rows: list[dict]) -> None:
    data_js = base / "data.js"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_HEADERS)
    w.writeheader()
    w.writerows(shopping_rows)
    csv_text = buf.getvalue()

    existing = data_js.read_text(encoding="utf-8") if data_js.exists() else ""
    existing = SHOPPING_BLOCK_PAT.sub("", existing)
    new_block = (
        "window.__SHOPPING_DATA = "
        + json.dumps(csv_text, ensure_ascii=False)
        + ";\n"
    )
    data_js.write_text(existing + new_block, encoding="utf-8")
    print(f"   已更新 → {data_js}（追加 __SHOPPING_DATA）")


# ============================================================
# 主流程
# ============================================================
def parse_only(argv: list[str]) -> Optional[set]:
    for i, a in enumerate(argv):
        if a.startswith("--only="):
            return set(a.split("=", 1)[1].split(","))
        if a == "--only" and i + 1 < len(argv):
            return set(argv[i + 1].split(","))
    return None


def estimate_cost(cats: dict) -> None:
    name_calls = sum(len(c["curated"]) for c in cats.values())
    kw_calls = sum(len(c["queries"]) for c in cats.values()) * 3   # 每組 query 最多翻 3 頁
    total = name_calls + kw_calls
    unit = 0.032
    bar = "=" * 60
    print(bar)
    print("📊 預估 API 費用上限")
    print("-" * 60)
    cat_list = "、".join(cats.keys())
    print(f"  本次蒐集子類別：{cat_list}")
    print(f"  方式 A（精選名單）：{name_calls} 次 × ${unit} = ${name_calls * unit:.2f}")
    print(f"  方式 B（關鍵字翻頁）：{kw_calls} 次 × ${unit} = ${kw_calls * unit:.2f}")
    print(f"  合計上限              ≈ ${total * unit:.2f} USD")
    print(bar)


def rebuild_from_csv() -> None:
    """從現有 cleaned/shopping.csv 重 dedup + 重寫 data.js（不打 API）。"""
    base = Path(__file__).resolve().parent
    src = base / "cleaned" / "shopping.csv"
    if not src.exists():
        sys.exit(f"錯誤：找不到 {src}，請先正常跑一次。")
    with open(src, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"📂 載入 {src} → {len(rows)} 列")
    deduped, dup = dedup_by_cid(rows)
    print(f"🔁 跨類別 dedup：去除 {dup} 筆 → {len(deduped)} 筆")
    with open(src, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        w.writerows(deduped)
    print(f"📁 已重寫 {src}")
    update_data_js(base, deduped)


def main() -> None:
    if "--rebuild-from-csv" in sys.argv:
        rebuild_from_csv()
        return

    only = parse_only(sys.argv)
    if only:
        unknown = only - set(SHOPPING_CATS.keys())
        if unknown:
            sys.exit(f"錯誤：--only 指定了未知子類別 {unknown}，現有：{list(SHOPPING_CATS.keys())}")
        cats = {k: v for k, v in SHOPPING_CATS.items() if k in only}
    else:
        cats = SHOPPING_CATS

    estimate_cost(cats)

    if "--dry-run" in sys.argv:
        print("（--dry-run 已啟用，不打 API、不寫檔）")
        return

    auto_yes = ("-y" in sys.argv) or ("--yes" in sys.argv)
    if auto_yes:
        print("（--yes 已啟用，跳過確認）")
    else:
        try:
            ans = input("確認開始收集？(y/N) > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return
        if ans != "y":
            print("已取消。")
            return

    base = Path(__file__).resolve().parent
    out_dir = base / "cleaned"
    out_dir.mkdir(exist_ok=True)
    out_csv = out_dir / "shopping.csv"

    all_rows: list[dict] = []
    for label, info in cats.items():
        print(f"\n🔍 [{label}]")
        # 方式 A
        curated_pairs = collect_curated(label, info["curated"])
        excluded_ids = {pid for pid, _ in curated_pairs if pid}
        print(f"  方式 A：{len(curated_pairs)} 間（dedup 用 place_id：{len(excluded_ids)}）")
        # 方式 B
        sup_rows = collect_supplemental(
            label, info["queries"], excluded_ids,
            target_total=TARGET_PER_CAT,
            already=len(curated_pairs),
        )
        rows = [r for _, r in curated_pairs] + sup_rows
        print(f"  ✅ [{label}] 共 {len(rows)} 間（精選 {len(curated_pairs)} + 補 {len(sup_rows)}）")
        all_rows.extend(rows)

    # 跨類別 dedup（同 cid 一筆勝出 — 順序在前的子類別優先）
    all_rows, dup = dedup_by_cid(all_rows)
    print(f"\n🔁 跨類別 dedup：去除 {dup} 筆重複 cid → 剩 {len(all_rows)} 筆")

    # 寫 shopping.csv
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"\n📁 已寫 {out_csv}（共 {len(all_rows)} 筆）")

    # 更新 data.js（追加 __SHOPPING_DATA）
    update_data_js(base, all_rows)

    actual = _TEXT_CALLS * 0.032
    print(f"\n💰 實際 Text Search call：{_TEXT_CALLS} 次 ≈ ${actual:.2f} USD")
    print("\n👉 雙擊 map.html 即可檢視（請先確認 map.html 已支援 __SHOPPING_DATA）。")


if __name__ == "__main__":
    main()
