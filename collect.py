#!/usr/bin/env python3
"""京都餐廳資料收集腳本

從 Google Places API (New) 抓京都各區域餐廳，
用直線距離 + Google 評分/評論數做品質過濾後輸出 all_restaurants.csv。

不再使用 Routes API。如果使用者切換需求要算實際交通時間，再依少量子集呼叫。

Usage:
    python collect.py [--yes] [--only=類別1,類別2]
"""

from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Windows cp950 終端吃不下 emoji，強制 UTF-8 stdout
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

# 京都車站座標（一般 Text Search 的 locationBias 中心，做為廣域 fallback）
KYOTO_STATION = {"latitude": 34.985849, "longitude": 135.758767}
SEARCH_RADIUS_M = 10_000  # 10 km

# 單類別最多收 N 間（品質過濾後仍超過時，按評論數截斷）
HARD_CAP = 1500

# 品質過濾門檻（每筆候選都會檢查；不過則直接丟）
MIN_RATING = 3.0
MIN_REVIEWS = 99

# 區域關鍵字 + 各自的 locationBias 中心（修正之前 bias 全部用京都駅造成偏遠區搜不到的 bug）
# 包含京都市內景點 / 神社 / 寺廟 + 主要 JR 站 + 京都府其他市町（宇治、長岡京、向日、久御山、八幡）
AREA_COORDS = {  # name -> (lat, lng, radius_m)
    # 中央區（観光熱點）
    "祇園":             (35.0036, 135.7757, 1500),
    "河原町":           (35.0046, 135.7686, 1500),
    "清水":             (34.9948, 135.7848, 1500),
    "二条城":           (35.0140, 135.7480, 1500),
    "三十三間堂":       (34.9879, 135.7716, 1500),
    "知恩院":           (35.0064, 135.7826, 1200),
    # 東邊一帶
    "下鴨":             (35.0398, 135.7727, 1500),
    "銀閣寺":           (35.0270, 135.7980, 1500),
    "一乗寺":           (35.0386, 135.7920, 1500),
    "平安神宮":         (35.0160, 135.7820, 1500),
    "南禅寺":           (35.0107, 135.7935, 1200),
    "醍醐寺":           (34.9510, 135.8190, 1500),
    "大原":             (35.1170, 135.8340, 2500),
    "山科駅":           (34.9852, 135.8138, 1800),
    # 北邊
    "北山":             (35.0537, 135.7547, 2000),
    "金閣寺":           (35.0394, 135.7290, 1500),
    "京都御所":         (35.0250, 135.7620, 1500),
    "北野天滿宮":       (35.0310, 135.7350, 1500),
    "上賀茂神社":       (35.0603, 135.7530, 1500),
    "鞍馬貴船":         (35.1200, 135.7670, 2500),
    "JR円町駅":         (35.0146, 135.7345, 1200),
    # 西邊
    "嵐山":             (35.0140, 135.6776, 2000),
    "太秦":             (35.0130, 135.7080, 1500),
    "仁和寺":           (35.0300, 135.7130, 1500),
    "龍安寺":           (35.0345, 135.7185, 1200),
    "桂離宮":           (34.9850, 135.7110, 2000),
    "JR桂川駅":         (34.9533, 135.7102, 1500),
    "JR二条駅":         (35.0099, 135.7396, 1200),
    # 南邊
    "伏見":             (34.9667, 135.7723, 2500),
    "東寺":             (34.9810, 135.7470, 1500),
    "JR桃山駅":         (34.9387, 135.7728, 1500),
    # 京都府其他市町（市區外）
    "宇治":             (34.8849, 135.7951, 2000),
    "平等院":           (34.8895, 135.8073, 1500),
    "黄檗":             (34.9143, 135.8056, 1500),
    "長岡京":           (34.9210, 135.6962, 2000),
    "向日":             (34.9505, 135.7066, 1500),
    "久御山":           (34.8704, 135.7474, 2000),
    "八幡":             (34.8783, 135.7079, 2000),
    "城陽":             (34.8650, 135.7785, 2000),
    # JR 嵯峨野線往龜岡
    "JR丹波口駅":       (34.9919, 135.7426, 1200),
    "JR保津峡駅":       (35.0218, 135.6347, 1500),
    "JR馬堀駅":         (35.0118, 135.5879, 1500),
    "JR亀岡駅":         (35.0143, 135.5779, 1800),
    # JR 東海道本線往大山崎 / 西大路
    "JR西大路駅":       (34.9789, 135.7330, 1500),
    "JR山崎駅":         (34.9028, 135.6800, 1800),
    # JR 奈良線（東福寺 → 祝園）
    "JR東福寺駅":       (34.9789, 135.7728, 1500),
    "JR六地蔵駅":       (34.9354, 135.8045, 1500),
    "JR木幡駅":         (34.9039, 135.8138, 1500),
    "JR小倉駅":         (34.8867, 135.7800, 1500),
    "JR新田駅":         (34.8762, 135.7796, 1500),
    "JR京田辺駅":       (34.8160, 135.7672, 1800),
    "JR松井山手駅":     (34.8095, 135.6892, 1800),
    "JR同志社前駅":     (34.8245, 135.7610, 1500),
    "JR祝園駅":         (34.7587, 135.7757, 2000),
    # 阪急京都線
    "阪急西院駅":       (35.0078, 135.7400, 1200),
    "阪急西京極駅":     (34.9989, 135.7308, 1200),
    "阪急桂駅":         (34.9810, 135.7048, 1500),
    "阪急洛西口駅":     (34.9622, 135.7115, 1500),
    "阪急長岡天神駅":   (34.9265, 135.6986, 1500),
    # 京阪本線
    "京阪出町柳駅":     (35.0296, 135.7728, 1200),
    "京阪七条駅":       (34.9897, 135.7716, 1200),
    "京阪中書島駅":     (34.9290, 135.7587, 1500),
    "京阪丹波橋駅":     (34.9425, 135.7641, 1500),
    # 京都市営地下鉄
    "烏丸御池駅":       (35.0102, 135.7591, 1200),
    "国際会館駅":       (35.0623, 135.7843, 1800),
    "北大路駅":         (35.0464, 135.7613, 1500),
    "今出川駅":         (35.0290, 135.7613, 1200),
    "太秦天神川駅":     (35.0125, 135.7222, 1500),
    # 叡山電鉄
    "修学院駅":         (35.0492, 135.7896, 1200),
    "宝ヶ池駅":         (35.0570, 135.7838, 1200),
}
AREA_KEYWORDS = list(AREA_COORDS.keys())

# 類別 → 多關鍵字（去重後合併）。`area_term` 用來組「<area_term> <area>」的區域關鍵字
CATEGORIES: dict[str, dict] = {
    "鰻魚飯": {
        "ja_label": "うなぎ",
        "area_term": "うなぎ",
        "queries": ["うなぎ 京都", "鰻 京都", "ひつまぶし 京都"],
    },
    "丼飯": {
        "ja_label": "丼",
        "area_term": "丼",
        "queries": [
            "丼 京都", "親子丼 京都", "海鮮丼 京都",
            "カツ丼 京都", "天丼 京都",
        ],
    },
    "拉麵": {
        "ja_label": "ラーメン",
        "area_term": "ラーメン",
        "queries": ["ラーメン 京都", "京都ラーメン", "つけ麺 京都"],
    },
    "抹茶甜點": {
        "ja_label": "抹茶スイーツ",
        "area_term": "抹茶",
        "queries": ["抹茶スイーツ 京都", "抹茶パフェ 京都", "抹茶 カフェ 京都"],
    },
    "燒烤": {
        "ja_label": "焼肉",
        "area_term": "焼肉",
        "queries": ["焼肉 京都", "和牛 京都", "ホルモン 京都"],
    },
    "居酒屋": {
        "ja_label": "居酒屋",
        "area_term": "居酒屋",
        "queries": ["居酒屋 京都", "焼き鳥 京都", "串焼き 京都"],
    },
    "甜點": {
        "ja_label": "スイーツ",
        "area_term": "スイーツ",
        "queries": [
            "スイーツ 京都", "パンケーキ 京都",
            "パフェ 京都", "スフレ 京都", "かき氷 京都",
        ],
    },
    "章魚燒": {
        "ja_label": "たこ焼き",
        "area_term": "たこ焼き",
        "queries": ["たこ焼き 京都", "たこやき 京都"],
    },
    "炸豬排": {
        "ja_label": "とんかつ",
        "area_term": "とんかつ",
        "queries": ["とんかつ 京都", "トンカツ 京都", "豚カツ 京都"],
    },
    "飯糰專賣": {
        "ja_label": "おにぎり",
        "area_term": "おにぎり",
        "queries": ["おにぎり 京都", "おむすび 京都", "おにぎり専門店 京都"],
    },
    "日式洋食": {
        "ja_label": "洋食",
        "area_term": "洋食",
        "queries": ["洋食 京都", "ハンバーグ 京都", "オムライス 京都"],
    },
    "日式早餐": {
        "ja_label": "和朝食",
        "area_term": "朝食",
        "queries": ["和朝食 京都", "京都 朝ごはん", "モーニング 京都"],
    },
    "川床料理": {
        "ja_label": "川床",
        "area_term": "川床",
        "queries": [
            "川床 京都", "納涼床 京都", "京都 床",
            "貴船 川床", "鴨川 納涼床", "高雄 川床",
            "京料理 川床",
        ],
    },
}

# 自動把每個類別的 queries 變成 [(text, bias), ...]
# 一般 query 用京都駅當 bias，區域 query 用該區自己當 bias
KYOTO_BIAS = {"circle": {"center": KYOTO_STATION, "radius": SEARCH_RADIUS_M}}

def _area_bias(lat: float, lng: float, radius_m: int) -> dict:
    return {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}}

for _cat, _info in CATEGORIES.items():
    _term = _info["area_term"]
    _general = [(q, KYOTO_BIAS) for q in _info["queries"]]
    _area = [
        (f"{_term} {_a}", _area_bias(_lat, _lng, _r))
        for _a, (_lat, _lng, _r) in AREA_COORDS.items()
    ]
    _info["queries"] = _general + _area

# Endpoint
TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# 實際 API call 計數（用於跑完印實際花費）
_TEXT_CALLS = 0

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
]


# ============================================================
# HTTP 工具
# ============================================================
def post_with_retry(
    url: str,
    headers: dict,
    body: dict,
    *,
    label: str = "",
    max_retry: int = 4,
) -> dict:
    """POST 並做 429/5xx exponential backoff 重試。"""
    delay = 1.0
    last_err: Optional[str] = None
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
        raise RuntimeError(
            f"{label} HTTP {r.status_code}: {r.text[:800]}"
        )
    raise RuntimeError(f"{label} 重試次數用盡：{last_err}")


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
           * math.sin(dlng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


# ============================================================
# Text Search
# ============================================================
def text_search_all(query: str, location_bias: dict, max_results: int = 60) -> list[dict]:
    """單一關鍵字翻頁抓取，最多 max_results 筆（Google 上限 60）。

    location_bias: 例如 KYOTO_BIAS 或 _area_bias(...) 產生的 dict
    """
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
        # 不限 includedType：抹茶 / 京菓子 / 咖啡店多被歸類為 cafe/bakery，限制 restaurant 會漏
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
        # nextPageToken 需要等待短暫時間才能生效
        time.sleep(2.0)
    return results


# ============================================================
# 整理 / 格式化
# ============================================================
def format_opening_hours(opening: Optional[dict]) -> str:
    if not opening:
        return ""
    weekday = opening.get("weekdayDescriptions") or []
    return " | ".join(weekday)


def first_photo_resource(photos: Optional[list]) -> str:
    if not photos:
        return ""
    return photos[0].get("name", "")


# ============================================================
# 費用預估
# ============================================================
def estimate_cost(cats_to_process: dict) -> None:
    keyword_count = sum(len(c["queries"]) for c in cats_to_process.values())
    text_calls = keyword_count * 3
    text_unit = 0.032
    text_cost = text_calls * text_unit

    bar = "=" * 60
    print(bar)
    print("📊 預估 API 費用上限")
    print("-" * 60)
    cat_list = "、".join(cats_to_process.keys())
    print(f"  本次蒐集：{cat_list}")
    print(f"  Text Search  ~{text_calls:>3d} 次  × ${text_unit:.3f} = ${text_cost:.2f}")
    print(f"  Routes API     0 次（用直線距離取代，不打 Routes）")
    print(f"  合計上限                              ≈ ${text_cost:.2f} USD")
    print(bar)
    print("（Google Maps 平台每月有 $200 免費額度，多數情況都吃得起）")


# ============================================================
# 主流程
# ============================================================
def collect_category(label: str, info: dict, cache: dict) -> tuple[list[dict], int]:
    """單一類別的完整蒐集流程：

    1) 對所有 keywords 跑 Text Search、依 place_id dedup
    2) 排序（評論數 desc → 評分 desc）
    3) 套品質過濾（評分、評論數、地址必須含「京都」）
    4) 過濾後若仍超過 HARD_CAP 才截斷（之前的 bug 是先截才過濾，會誤殺好店）
    5) 直線距離由 Haversine 計算；交通時間若 cache 有就沿用，否則留空
    """
    print(f"\n🔍 [{label}] 開始蒐集（關鍵字 {len(info['queries'])} 組）")
    seen: dict[str, dict] = {}
    for q_text, q_bias in info["queries"]:
        try:
            places = text_search_all(q_text, q_bias, max_results=60)
        except Exception as e:
            print(f"  ⚠ 「{q_text}」抓取失敗：{e}")
            continue
        new = 0
        for p in places:
            pid = p.get("id")
            if pid and pid not in seen:
                seen[pid] = p
                new += 1
        print(f"  「{q_text}」+{new}（累計 {len(seen)} 間）")
        time.sleep(0.3)

    # 排序：評論數高 > 評分高
    candidates = sorted(
        seen.values(),
        key=lambda p: (p.get("userRatingCount") or 0, p.get("rating") or 0),
        reverse=True,
    )

    rows: list[dict] = []
    quality_skip = 0
    not_kyoto = 0
    no_coord = 0
    cache_hits = 0
    capped = 0
    kyoto_lat = KYOTO_STATION["latitude"]
    kyoto_lng = KYOTO_STATION["longitude"]

    for p in candidates:
        loc = p.get("location") or {}
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        name = (p.get("displayName") or {}).get("text", "")
        addr = p.get("formattedAddress", "")

        if lat is None or lng is None:
            no_coord += 1
            continue
        # 地址過濾：locationBias 只是 soft hint，會混進 静岡県清水区 / 埼玉嵐山町 等同名地點
        # 注意：必須查「京都府」，不能用「京都」當子字串 —— 東京都也含「京都」會誤放行！
        if "京都府" not in addr:
            not_kyoto += 1
            continue

        rating = p.get("rating") or 0
        reviews = p.get("userRatingCount") or 0
        if rating and rating < MIN_RATING:
            quality_skip += 1
            continue
        if reviews < MIN_REVIEWS:
            quality_skip += 1
            continue

        # 過了所有門檻才檢查 HARD_CAP（先截才過濾會誤殺）
        if len(rows) >= HARD_CAP:
            capped += 1
            continue

        cache_key = (round(lat, 5), round(lng, 5))
        cached = cache.get(cache_key)
        if cached:
            cache_hits += 1
            tm = cached.get("交通時間（分鐘）", "")
            wm = cached.get("步行時間（分鐘）", "")
            tr = cached.get("換乘次數", "")
        else:
            tm = wm = tr = ""

        distance_km = round(haversine_km(kyoto_lat, kyoto_lng, lat, lng), 2)

        rows.append({
            "類別": label,
            "店名": name,
            "日文店名": name,
            "地址": addr,
            "緯度": lat,
            "經度": lng,
            "直線距離（km）": distance_km,
            "評分": rating or "",
            "評論數": reviews or "",
            "價位等級": PRICE_LEVEL_MAP.get(p.get("priceLevel", ""), ""),
            "營業時間": format_opening_hours(p.get("regularOpeningHours")),
            "交通時間（分鐘）": tm,
            "步行時間（分鐘）": wm,
            "換乘次數": tr,
            "Google Maps連結": p.get("googleMapsUri", ""),
            "店家網站": p.get("websiteUri", ""),
            "照片資源": first_photo_resource(p.get("photos")),
        })

    print(
        f"  ✅ [{label}] 收齊 {len(rows)} 間 / 候選 {len(candidates)} "
        f"（品質掉 {quality_skip}，非京都 {not_kyoto}，無座標 {no_coord}，"
        f"超過 HARD_CAP {capped}，cache 命中 {cache_hits}）"
    )
    return rows, cache_hits


def write_outputs(all_rows: list[dict], base: Path) -> tuple[Path, Path]:
    """寫 CSV + data.js。可被中途呼叫做 incremental save。"""
    out_path = base / "all_restaurants.csv"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(all_rows)

    import io, json as _json
    buf = io.StringIO()
    writer2 = csv.DictWriter(buf, fieldnames=CSV_HEADERS)
    writer2.writeheader()
    writer2.writerows(all_rows)
    csv_text = buf.getvalue()
    data_js_path = base / "data.js"
    with open(data_js_path, "w", encoding="utf-8") as f:
        f.write("window.__CSV_DATA = " + _json.dumps(csv_text, ensure_ascii=False) + ";\n")
    return out_path, data_js_path


def parse_only(argv: list[str]) -> Optional[set]:
    """支援 --only=甜點,拉麵 或 --only 甜點,拉麵 兩種寫法。"""
    for i, a in enumerate(argv):
        if a.startswith("--only="):
            return set(a.split("=", 1)[1].split(","))
        if a == "--only" and i + 1 < len(argv):
            return set(argv[i + 1].split(","))
    return None


def load_existing_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_coord_cache(rows: list[dict]) -> dict:
    """以四捨五入到第 5 位的 (lat, lng) 為 key 建立 cache（精度約 1m）。
    分店本店的座標一定不同，自然分開。要求列必須有有效的交通時間才算可重用。
    """
    cache: dict = {}
    for r in rows:
        try:
            lat = float(r.get("緯度") or "")
            lng = float(r.get("經度") or "")
            tm = int(r.get("交通時間（分鐘）") or 0)
        except (ValueError, TypeError):
            continue
        if tm <= 0:
            continue
        cache[(round(lat, 5), round(lng, 5))] = r
    return cache


def main() -> None:
    only = parse_only(sys.argv)
    if only:
        unknown = only - set(CATEGORIES.keys())
        if unknown:
            sys.exit(f"錯誤：--only 指定了未知類別 {unknown}，現有：{list(CATEGORIES.keys())}")
        cats_to_process = {k: v for k, v in CATEGORIES.items() if k in only}
    else:
        cats_to_process = CATEGORIES

    estimate_cost(cats_to_process)
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
    out_path = base / "all_restaurants.csv"

    # 載入既有 CSV → 建座標 cache，可沿用既有 transit 欄位（早期 Routes 結果）
    existing = load_existing_csv(out_path)
    cache = build_coord_cache(existing)
    print(f"\n📦 載入既有 {len(existing)} 筆，可沿用 transit 資料 {len(cache)} 個座標")

    # 增量模式：保留 CSV 中不在本次蒐集範圍的類別
    if only:
        kept = [r for r in existing if r.get("類別") not in only]
        print(f"📋 增量模式：保留既有 {len(kept)} 筆（不含 {only}）")
    else:
        kept = []

    new_rows: list[dict] = []
    total_hits = 0
    for label, info in cats_to_process.items():
        rows, hits = collect_category(label, info, cache)
        new_rows.extend(rows)
        total_hits += hits
        all_rows = kept + new_rows
        write_outputs(all_rows, base)
        print(f"  💾 已存檔（總計 {len(all_rows)} 筆，cache 累計命中 {total_hits}）")

    all_rows = kept + new_rows
    out_path, data_js_path = write_outputs(all_rows, base)
    actual_cost = _TEXT_CALLS * 0.032
    print(f"\n📁 raw 輸出：")
    print(f"   - {out_path}（共 {len(all_rows)} 筆，沿用既有 transit 資料 {total_hits} 筆）")
    print(f"   - {data_js_path}（待清理）")
    print(f"\n💰 實際 Text Search call 數：{_TEXT_CALLS} 次 ≈ ${actual_cost:.2f} USD")

    # 自動清理：去重、砍地標 / 飯店 / 車站 / 商場、分離景點
    print("\n🧹 自動清理（cid 去重 + 三層過濾 + 景點分離）...")
    try:
        from clean_csv import main as cleanup
        cleanup(apply_overwrite=True)
    except Exception as e:
        print(f"⚠ 清理失敗：{e}")
        print(f"   raw CSV 已保留。可手動跑：python clean_csv.py --apply")
    print("\n👉 雙擊 map.html 即可檢視。")


if __name__ == "__main__":
    main()
