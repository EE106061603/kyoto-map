#!/usr/bin/env python3
"""KIX（關西機場）周邊資料收集腳本

最後一天住 KIX 機場套房（りんくうタウン站旁），把行李寄在 JR りんくうタウン駅
（改札內 10 處 coin locker，5:30–23:30），從那站搭 JR 関空快速 / 紀州路快速約
20 分鐘可達範圍：関西空港 → りんくうタウン → 日根野 → 熊取 → 和泉砂川 → 東岸和田。

抓 8 個類別：拉麵、甜點 + 6 子類別購物（職人專門/選物生活/文具/百貨/超市/扭蛋）。
為了不污染既有「京都府限定」的 collect.py / collect_shopping.py 邏輯，獨立腳本走自己的
過濾（地址必須含「大阪府」+ 在 KIX 周邊 area_bias 圓心 N km 內）。

輸出：
    cleaned/kix.csv      欄位與 cleaned/shopping.csv 一致（含「精選」「兼類別」空欄）
    data.js              追加 window.__KIX_DATA（map.html file:// 雙擊讀）

Usage:
    python collect_kix.py --dry-run    # 印 API 預估，不打 API
    python collect_kix.py --yes        # 直接跑（跳過確認）
    python collect_kix.py              # 跑（會問確認）
    python collect_kix.py --only=拉麵,甜點
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

# 寄物櫃中心 = JR りんくうタウン駅（KIX 機場套房就在它旁邊；改札內 10 處 locker）
RINKU_STATION = {"latitude": 34.4147, "longitude": 135.2952}

# 從 JR りんくうタウン 搭 関空快速 / 紀州路快速 約 20 分鐘可達的主要站
# 半徑 1.5–2 km cover 站週邊商圈，避免 area_bias 太大被稀釋
# 精選 5 站：りんくう / 機場 / 泉佐野 / 日根野 / 熊取（捨棄較遠的和泉砂川 / 東岸和田，
# in_kix_zone 的 13km 距離過濾仍會放行那邊符合的店）
AREA_COORDS_KIX: dict[str, tuple[float, float, int]] = {
    "りんくうタウン駅":   (34.4147, 135.2952, 2000),
    "関西空港駅":         (34.4347, 135.2440, 1500),
    "泉佐野駅":           (34.4072, 135.3197, 1800),  # 南海主站，商圈大
    "日根野駅":           (34.4002, 135.3304, 1500),  # JR 関空快速分歧
    "熊取駅":             (34.3868, 135.3592, 1500),
}

# 每類別目標筆數（過品質後超過就截斷）
TARGET_PER_CAT = 25

# 品質門檻
MIN_RATING = 3.5
MIN_REVIEWS = 30

# 8 個 KIX 類別。area_term 拿來組「{area_term} {area_name}」query
# 直接以 area_bias 為主、不像 collect_shopping.py 還有「精選名單」模式
# （大阪在地店家清單我不熟，靠關鍵字 + bias 拉就好）
KIX_CATS: dict[str, dict] = {
    "拉麵":       {"area_term": "ラーメン",   "extra": ["つけ麺"]},
    "甜點":       {"area_term": "スイーツ",   "extra": ["カフェ"]},
    "職人專門":   {"area_term": "専門店",     "extra": []},
    "選物生活":   {"area_term": "雑貨",       "extra": []},
    "文具":       {"area_term": "文房具",     "extra": []},
    "百貨":       {"area_term": "ショッピングモール", "extra": ["アウトレット"]},
    "超市":       {"area_term": "スーパー",   "extra": []},
    "扭蛋":       {"area_term": "ガシャポン", "extra": []},
}

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

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

# 與 cleaned/shopping.csv 對齊（含精選 / 兼類別兩欄，雖然 KIX 不會用到）
CSV_HEADERS = [
    "類別", "店名", "日文店名", "地址", "緯度", "經度",
    "直線距離（km）",
    "評分", "評論數", "價位等級", "營業時間",
    "交通時間（分鐘）", "步行時間（分鐘）", "換乘次數",
    "Google Maps連結", "店家網站", "照片資源",
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


def _area_bias(lat, lng, radius_m):
    return {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}}


# ============================================================
# Text Search
# ============================================================
def text_search_paged(query: str, location_bias: dict, *, max_results: int = 60) -> list[dict]:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": TEXT_SEARCH_FIELDS,
    }
    body_base = {
        "textQuery": query,
        "languageCode": "ja",
        "regionCode": "JP",
        "locationBias": location_bias,
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
    # KIX 用「離 JR りんくうタウン駅 直線距離」當參考（不是離京都駅）
    distance_km = (
        round(haversine_km(RINKU_STATION["latitude"], RINKU_STATION["longitude"], lat, lng), 2)
        if (lat is not None and lng is not None) else ""
    )
    return {
        "類別": label,
        "店名": name,
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
# 大阪府泉南郡 / 泉佐野市 / 泉南市 / 阪南市 / 熊取町 / 田尻町 / 岸和田市 / 貝塚市 / 和泉市 等
# locationBias 是 soft hint，會把大阪市區甚至兵庫的店混進來，地址過濾必須嚴格
KIX_AREA_KW = [
    "大阪府泉佐野市", "大阪府泉南市", "大阪府阪南市", "大阪府岸和田市",
    "大阪府貝塚市", "大阪府和泉市",
    "泉南郡熊取町", "泉南郡田尻町", "泉南郡岬町",
]

# 進一步限制：必須在 JR りんくうタウン駅 直線 KIX_MAX_KM 公里內
# JR 関空快速 20 分鐘 → 沿線到東岸和田約 11 km，留 buffer 取 13 km
KIX_MAX_KM = 13.0


def in_kix_zone(addr: str, lat: Optional[float], lng: Optional[float]) -> bool:
    """是否屬於 KIX 周邊（地址含關鍵市町 + 距離 りんくう ≤ KIX_MAX_KM）"""
    if not addr or "大阪府" not in addr:
        return False
    if not any(kw in addr for kw in KIX_AREA_KW):
        return False
    if lat is None or lng is None:
        return False
    d = haversine_km(RINKU_STATION["latitude"], RINKU_STATION["longitude"], lat, lng)
    return d <= KIX_MAX_KM


# 名稱結尾為車站 / 高樓建物 → 直接丟（Google Places 對「ラーメン 泉佐野駅」會回車站本體）
# 不像 clean_csv.py 那麼細，因為 KIX 周邊資料量小，幾個關鍵 suffix 抓得乾淨即可
JUNK_NAME_PAT = re.compile(r"(駅|Station|タワー|Tower|ATM|郵便局|交番|警察署)$")


def is_junk_name(name: str) -> bool:
    return bool(JUNK_NAME_PAT.search(name or ""))


def collect_category(label: str, info: dict) -> list[dict]:
    print(f"\n🔍 [{label}]")
    seen: dict[str, dict] = {}
    terms = [info["area_term"]] + list(info.get("extra") or [])
    for term in terms:
        for area, (lat, lng, radius) in AREA_COORDS_KIX.items():
            q = f"{term} {area}"
            try:
                places = text_search_paged(q, _area_bias(lat, lng, radius), max_results=60)
            except Exception as e:
                print(f"  ⚠ 「{q}」搜尋失敗：{e}")
                continue
            new = 0
            for p in places:
                pid = p.get("id")
                if pid and pid not in seen:
                    seen[pid] = p
                    new += 1
            print(f"    「{q}」+{new}（累計 {len(seen)}）")
            time.sleep(0.3)

    # 排序：評論數 desc → 評分 desc
    candidates = sorted(
        seen.values(),
        key=lambda p: (p.get("userRatingCount") or 0, p.get("rating") or 0),
        reverse=True,
    )

    rows: list[dict] = []
    not_kix = quality_skip = no_coord = junk = 0
    for p in candidates:
        if len(rows) >= TARGET_PER_CAT:
            break
        loc = p.get("location") or {}
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        addr = p.get("formattedAddress") or ""
        name = (p.get("displayName") or {}).get("text", "")
        if lat is None or lng is None:
            no_coord += 1
            continue
        if not in_kix_zone(addr, lat, lng):
            not_kix += 1
            continue
        if is_junk_name(name):
            junk += 1
            continue
        rating = p.get("rating") or 0
        reviews = p.get("userRatingCount") or 0
        if rating and rating < MIN_RATING:
            quality_skip += 1
            continue
        if reviews < MIN_REVIEWS:
            quality_skip += 1
            continue
        rows.append(to_row(label, p))

    print(
        f"  ✅ [{label}] 收齊 {len(rows)}/{TARGET_PER_CAT} 間 / 候選 {len(candidates)} "
        f"（非 KIX 區 {not_kix}、車站/建物 {junk}、品質不足 {quality_skip}、無座標 {no_coord}）"
    )
    return rows


# ============================================================
# 跨類別 dedup（同 cid 第一個贏；KIX_CATS 順序即優先序）
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
# data.js 更新（不動既有 __CSV_DATA / __ATTRACTIONS_DATA / __SHOPPING_DATA）
# ============================================================
KIX_BLOCK_PAT = re.compile(r"window\.__KIX_DATA\s*=\s*.*?;\s*\n", re.S)


def update_data_js(base: Path, kix_rows: list[dict]) -> None:
    data_js = base / "data.js"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_HEADERS)
    w.writeheader()
    w.writerows(kix_rows)
    csv_text = buf.getvalue()

    existing = data_js.read_text(encoding="utf-8") if data_js.exists() else ""
    existing = KIX_BLOCK_PAT.sub("", existing)
    new_block = (
        "window.__KIX_DATA = "
        + json.dumps(csv_text, ensure_ascii=False)
        + ";\n"
    )
    data_js.write_text(existing + new_block, encoding="utf-8")
    print(f"   已更新 → {data_js}（追加 __KIX_DATA）")


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
    n_areas = len(AREA_COORDS_KIX)
    queries = sum(1 + len(c.get("extra") or []) for c in cats.values())
    text_calls = queries * n_areas * 3   # 每組 query × 每 area × 最多翻 3 頁
    unit = 0.032
    bar = "=" * 60
    print(bar)
    print("📊 預估 API 費用上限")
    print("-" * 60)
    print(f"  本次蒐集：{', '.join(cats.keys())}")
    print(f"  area_bias 中心數：{n_areas}（JR りんくう 20 分內主要站）")
    print(f"  Text Search 上限 ~{text_calls} 次 × ${unit} = ${text_calls * unit:.2f}")
    print(f"  （實際因翻頁不滿，常約上限的 50–60%）")
    print(bar)


def rebuild_from_csv() -> None:
    """從現有 cleaned/kix.csv 重 junk 過濾 + dedup + 重寫 data.js（不打 API）。"""
    base = Path(__file__).resolve().parent
    src = base / "cleaned" / "kix.csv"
    if not src.exists():
        sys.exit(f"錯誤：找不到 {src}，請先正常跑一次。")
    with open(src, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"📂 載入 {src} → {len(rows)} 列")
    cleaned = [r for r in rows if not is_junk_name(r.get("日文店名") or r.get("店名") or "")]
    junk = len(rows) - len(cleaned)
    if junk:
        print(f"🧹 砍車站/建物：{junk} 筆")
    deduped, dup = dedup_by_cid(cleaned)
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
        unknown = only - set(KIX_CATS.keys())
        if unknown:
            sys.exit(f"錯誤：--only 指定了未知類別 {unknown}，現有：{list(KIX_CATS.keys())}")
        cats = {k: v for k, v in KIX_CATS.items() if k in only}
    else:
        cats = KIX_CATS

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
    out_csv = out_dir / "kix.csv"

    all_rows: list[dict] = []
    for label, info in cats.items():
        rows = collect_category(label, info)
        all_rows.extend(rows)

    all_rows, dup = dedup_by_cid(all_rows)
    print(f"\n🔁 跨類別 dedup：去除 {dup} 筆重複 cid → 剩 {len(all_rows)} 筆")

    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"\n📁 已寫 {out_csv}（共 {len(all_rows)} 筆）")

    update_data_js(base, all_rows)

    actual = _TEXT_CALLS * 0.032
    print(f"\n💰 實際 Text Search call：{_TEXT_CALLS} 次 ≈ ${actual:.2f} USD")
    print("\n👉 雙擊 map.html 即可檢視（請先確認 map.html 已支援 __KIX_DATA + 大阪 KIX 分頁）。")


if __name__ == "__main__":
    main()
