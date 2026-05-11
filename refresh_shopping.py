#!/usr/bin/env python3
"""購物資料修正流程（一次跑完）：

1. SUBCAT_REASSIGN：手動移類別（不打 API）
   - TRAVELER'S FACTORY KYOTO → 文具
   - ジェイアール京都伊勢丹 / 京都BAL / 京都ポルタ → 百貨
   - 丸善 京都本店 → 書店（新類別）

2. REFRESH：對 6 家精選用新 query 重抓 API（覆蓋舊 row）

3. DOUBLECHECK：對 3 家評論異常少的店重抓比對；新評論 > 舊才覆蓋，
   否則保留並寫 note「需人工確認」

4. 加「精選」「兼類別」欄位：
   - 精選：對 35 + 1（永楽屋 本店・カフェ）個 curated 名單對應 row 標 TRUE
   - 兼類別：跨檔比對 all_restaurants.csv 找同 cid 的餐廳分類

5. dedup by cid（精選優先保留）→ 重寫 shopping.csv → 更新 data.js

Usage:
    python refresh_shopping.py --dry-run    # 印預估、不打 API、不寫檔
    python refresh_shopping.py --yes        # 跑全流程（會打 API）
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ============================================================
# 設定
# ============================================================
load_dotenv()
API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
if not API_KEY:
    sys.exit("錯誤：找不到 GOOGLE_MAPS_API_KEY，請檢查 .env")

KYOTO_STATION = {"latitude": 34.985849, "longitude": 135.758767}
KYOTO_BIAS = {"circle": {"center": KYOTO_STATION, "radius": 10_000}}

BASE = Path(__file__).resolve().parent
SHOPPING_CSV = BASE / "cleaned" / "shopping.csv"
RESTAURANTS_CSV = BASE / "all_restaurants.csv"

# 既有 schema + 新增「精選」「兼類別」
BASE_HEADERS = [
    "類別", "店名", "日文店名", "地址", "緯度", "經度",
    "直線距離（km）",
    "評分", "評論數", "價位等級", "營業時間",
    "交通時間（分鐘）", "步行時間（分鐘）", "換乘次數",
    "Google Maps連結", "店家網站", "照片資源",
]
EXTRA_HEADERS = ["精選", "兼類別"]
SHOPPING_HEADERS = BASE_HEADERS + EXTRA_HEADERS

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
TEXT_SEARCH_FIELDS = ",".join([
    "places.id", "places.displayName", "places.formattedAddress",
    "places.location", "places.rating", "places.userRatingCount",
    "places.priceLevel", "places.regularOpeningHours",
    "places.googleMapsUri", "places.websiteUri", "places.photos",
])
PRICE_LEVEL_MAP = {
    "PRICE_LEVEL_FREE": "免費",
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
}

# ============================================================
# normalize / find_match（與 shopping_report.py 保持一致）
# ============================================================
VARIANT_MAP = str.maketrans({
    "衞": "衛", "栄": "榮", "彦": "彥", "高": "髙",
    "・": "", "·": "", "•": "",
})

def normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(c))
    s = s.translate(VARIANT_MAP)
    s = s.lower()
    s = re.sub(r"[\s\-_\.&（）()\[\]『』「」、。,／/]", "", s)
    s = s.replace("站", "駅")
    return s


def find_row(rows: list[dict], substr: str) -> tuple[int, dict] | None:
    """從 rows 找 normalized name 含 normalize(substr) 的列，回 (index, row)。
    多個命中時取 normalized name 最短（精準度高）。"""
    target = normalize(substr)
    if not target:
        return None
    cands: list[tuple[int, int, dict]] = []
    for i, r in enumerate(rows):
        v = normalize((r.get("日文店名") or "") + (r.get("店名") or ""))
        if target in v:
            cands.append((len(v), i, r))
    if not cands:
        return None
    cands.sort()
    return (cands[0][1], cands[0][2])


# ============================================================
# Places API
# ============================================================
_TEXT_CALLS = 0


def post_with_retry(body: dict, *, label: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": TEXT_SEARCH_FIELDS,
    }
    delay = 1.0
    for attempt in range(1, 5):
        try:
            r = requests.post(TEXT_SEARCH_URL, headers=headers, json=body, timeout=30)
        except requests.exceptions.RequestException as e:
            if attempt == 4:
                raise
            print(f"  ⚠ {label} 連線錯誤 {e}，{delay:.0f}s 後重試")
            time.sleep(delay); delay *= 2
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
            print(f"  ⚠ {label} HTTP {r.status_code}，{delay:.0f}s 後重試")
            time.sleep(delay); delay *= 2
            continue
        raise RuntimeError(f"{label} HTTP {r.status_code}: {r.text[:600]}")


def text_search_one(query: str) -> Optional[dict]:
    """取第一筆地址含「京都府」的 place，回完整 place dict。"""
    global _TEXT_CALLS
    _TEXT_CALLS += 1
    body = {
        "textQuery": query,
        "languageCode": "ja",
        "regionCode": "JP",
        "locationBias": KYOTO_BIAS,
        "pageSize": 5,
    }
    data = post_with_retry(body, label=f"Search[{query}]")
    for p in data.get("places") or []:
        if "京都府" in (p.get("formattedAddress") or ""):
            return p
    return None


def haversine_km(lat1, lng1, lat2, lng2):
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
           * math.sin(dlng/2)**2)
    return 2 * R * math.asin(math.sqrt(a))


def place_to_row(place: dict, label: str) -> dict:
    loc = place.get("location") or {}
    lat = loc.get("latitude")
    lng = loc.get("longitude")
    name = (place.get("displayName") or {}).get("text", "")
    addr = place.get("formattedAddress", "")
    distance = (
        round(haversine_km(KYOTO_STATION["latitude"], KYOTO_STATION["longitude"], lat, lng), 2)
        if lat is not None and lng is not None else ""
    )
    opening = place.get("regularOpeningHours") or {}
    hours_str = " | ".join(opening.get("weekdayDescriptions") or [])
    photos = place.get("photos") or []
    return {
        "類別": label,
        "店名": name,
        "日文店名": name,
        "地址": addr,
        "緯度": lat if lat is not None else "",
        "經度": lng if lng is not None else "",
        "直線距離（km）": distance,
        "評分": place.get("rating") or "",
        "評論數": place.get("userRatingCount") or "",
        "價位等級": PRICE_LEVEL_MAP.get(place.get("priceLevel", ""), ""),
        "營業時間": hours_str,
        "交通時間（分鐘）": "",
        "步行時間（分鐘）": "",
        "換乘次數": "",
        "Google Maps連結": place.get("googleMapsUri", ""),
        "店家網站": place.get("websiteUri", ""),
        "照片資源": (photos[0].get("name", "") if photos else ""),
        "精選": "",
        "兼類別": "",
    }


def get_cid(url: str) -> str:
    m = re.search(r"cid=(\d+)", url or "")
    return m.group(1) if m else ""


# ============================================================
# 子類別 reassign（不打 API）
# ============================================================
SUBCAT_REASSIGN = [
    # (用以識別舊 row 的 normalized substring, 新類別)
    ("TRAVELER'S FACTORY KYOTO",   "文具"),
    ("ジェイアール京都伊勢丹",     "百貨"),
    ("京都BAL",                    "百貨"),
    ("京都ポルタ",                 "百貨"),
    ("丸善 京都本店",              "文具"),
    # ハンズ京都店、京都ロフト 用戶指定保留為「選物生活」，不動
]

# ============================================================
# REFRESH：6 家用新 query 重抓
# ============================================================
REFRESH = [
    {"name": "永楽屋 細辻伊兵衛商店",
     "query": "永楽屋 細辻伊兵衛商店 河原町 本店",
     "old_substr": "永楽屋 細辻伊兵衛商店 京都駅八条口店"},
    {"name": "中川政七商店 京都本店",
     "query": "中川政七商店 京都本店 六角",
     "old_substr": "中川政七商店 ジェイアール京都伊勢丹店"},
    {"name": "松榮堂",
     "query": "香老舗 松栄堂 京都本店",
     "old_substr": "松栄堂 薫々"},
    {"name": "Smith 京都 Porta",
     "query": "Smith Kyoto Porta 文具",
     "old_substr": None,            # 京都ポルタ 已 reassign 到百貨，這裡是純 add
     "add_to_cat": "文具"},
    {"name": "Angers Kyoto",
     "query": "ANGERS 河原町本店",
     "old_substr": "ＡＮＧＥＲＳ"},
    {"name": "SOU・SOU 本店",
     "query": "SOU・SOU 着衣 新京極",
     "old_substr": "SOU・SOU Yousou."},
]

# ============================================================
# DOUBLECHECK：3 家評論異常少 → 比較新舊 row
# ============================================================
DOUBLECHECK = [
    {"name": "graf 京都",                 "query": "graf 京都 セレクトショップ", "old_substr": "グラフ"},
    {"name": "Pass The Baton 京都祇園店", "query": "PASS THE BATON 祇園 古美術", "old_substr": "Pass The Baton"},
    {"name": "嵩山堂はし本",              "query": "嵩山堂はし本 寺町",          "old_substr": "嵩山堂 はし本"},
]

# ============================================================
# 精選名單（標精選 = TRUE 用）
# ============================================================
CURATED_NAMES = {
    "職人專門": [
        "市原平兵衛商店", "永楽屋 細辻伊兵衛商店", "開化堂", "象彥",
        "松榮堂", "山田松香木店", "鳩居堂 京都本店", "有次",
        "唐草屋", "むす美 京都店", "一澤信三郎帆布", "朝日堂",
        "宮脇賣扇庵", "白竹堂 京都本店",
    ],
    "選物生活": [
        "D&Department KYOTO", "中川政七商店 京都本店",
        "SOU・SOU 本店", "SOU・SOU 足袋", "SOU・SOU 布袋",
        "Angers Kyoto", "graf 京都", "Pass The Baton 京都祇園店",
        "mina perhonen 京都", "45R 京都店",
    ],
    "文具": ["TRAVELER'S FACTORY KYOTO", "裏具", "嵩山堂はし本", "Smith 京都 Porta"],
    "百貨": ["京都伊勢丹", "髙島屋 京都店", "大丸 京都店", "京都 BAL",
              "新風館", "京都站 The Cube", "京都站 Porta"],
}
# 額外補精選：永楽屋 本店・カフェ（issue 5）
EXTRA_CURATED_SUBSTR = ["永楽屋 本店・カフェ"]

# 對映 Google Places displayName 與 curated 名稱對應不上的個案
ALIAS: dict[str, str] = {
    # refresh 之後的精選名稱已經改了 row 的 displayName，這裡只放
    # 「不會被 refresh 動到的精選」對應 alias。refresh 過的 row 在 step 2/3
    # 自帶精選=TRUE，step 4 對它們找不到舊 alias 是預期行為（不影響結果）。
    "京都站 The Cube": "京都ポルタ",
    "京都站 Porta": "京都ポルタ",
    "京都伊勢丹": "ジェイアール京都伊勢丹",
    "髙島屋 京都店": "京都髙島屋S.C.",
    "鳩居堂 京都本店": "京都鳩居堂",
    "白竹堂 京都本店": "白竹堂 本店",
    "45R 京都店": "45R 京都",
    # mina perhonen 的 row name 含「Kyoto」，curated 寫「京都」，normalize 後對不上
    "mina perhonen 京都": "minä perhonen Kyoto",
    # Pass The Baton row 名為 "Pass The Baton"（無京都祇園店後綴）
    "Pass The Baton 京都祇園店": "Pass The Baton",
}


# ============================================================
# 主流程
# ============================================================
def main() -> None:
    dry = "--dry-run" in sys.argv
    auto_yes = ("-y" in sys.argv) or ("--yes" in sys.argv)
    skip_api = "--skip-api" in sys.argv   # 跳過 step 1-3，只跑 step 4-7（事後補精選/兼類別/dedup）

    # API 預估
    n_calls = 0 if skip_api else len(REFRESH) + len(DOUBLECHECK)
    print("=" * 60)
    if skip_api:
        print("📊 --skip-api：跳過 reassign 與 API 重抓，只跑 step 4-7")
    else:
        print(f"📊 預估 API 呼叫：{n_calls} 次（refresh {len(REFRESH)} + doublecheck {len(DOUBLECHECK)}）")
        print(f"          ≈ ${n_calls * 0.032:.2f} USD")
    print("=" * 60)
    if dry:
        print("--dry-run，不打 API")
        return
    if not auto_yes:
        try:
            ans = input("確認執行？(y/N) > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("已取消"); return
        if ans != "y":
            print("已取消"); return

    # 載入 shopping.csv
    with open(SHOPPING_CSV, encoding="utf-8-sig") as f:
        rows: list[dict] = list(csv.DictReader(f))
    # 補齊新欄位（既有 row 沒這欄則填空）
    for r in rows:
        r.setdefault("精選", "")
        r.setdefault("兼類別", "")
    print(f"\n📂 載入 shopping.csv：{len(rows)} 列")

    # ============================================================
    # Step 1: 子類別 reassign（skip_api 時跳過 — 已是 reassign 過的狀態）
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 1：子類別 reassign（不打 API）")
    print("=" * 60)
    if skip_api:
        print("  --skip-api 跳過")
    for substr, new_cat in (SUBCAT_REASSIGN if not skip_api else []):
        m = find_row(rows, substr)
        if m is None:
            print(f"  ⚠ 找不到「{substr}」，略過")
            continue
        idx, r = m
        old_cat = r["類別"]
        if old_cat == new_cat:
            print(f"  · {r['日文店名']}：類別已是「{new_cat}」，不動")
        else:
            r["類別"] = new_cat
            print(f"  ✓ {r['日文店名']}：{old_cat} → {new_cat}")

    # ============================================================
    # Step 2: REFRESH（重抓 6 家）
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 2：REFRESH 重抓 6 家")
    print("=" * 60)
    if skip_api:
        print("  --skip-api 跳過")
    for item in (REFRESH if not skip_api else []):
        nm = item["name"]
        q = item["query"]
        old_substr = item.get("old_substr")
        try:
            place = text_search_one(q)
        except Exception as e:
            print(f"  ❌ 「{nm}」query 失敗：{e}")
            continue
        if not place:
            print(f"  ❌ 「{nm}」query「{q}」找不到京都府結果，略過")
            continue
        new_name = (place.get("displayName") or {}).get("text", "")
        new_cid = get_cid(place.get("googleMapsUri", ""))
        new_reviews = place.get("userRatingCount") or 0

        if old_substr is None:
            # add 模式
            cat = item.get("add_to_cat", "文具")
            new_row = place_to_row(place, cat)
            new_row["精選"] = "TRUE"
            rows.append(new_row)
            print(f"  ✓ {nm} → 新增「{new_name}」⭐{place.get('rating', '-')}（{new_reviews}）→ 類別 {cat}")
            continue

        # replace 模式
        m = find_row(rows, old_substr)
        if m is None:
            # 舊 row 找不到（可能上面 reassign 把名稱改了？實際上 reassign 不改名）
            # 這時當作 add：用既有同類別的子類別
            print(f"  ⚠ 「{nm}」找不到舊 row「{old_substr}」，當作新增")
            cat = "選物生活"  # 預設
            new_row = place_to_row(place, cat)
            new_row["精選"] = "TRUE"
            rows.append(new_row)
            continue
        idx, old_row = m
        cat = old_row["類別"]
        new_row = place_to_row(place, cat)
        new_row["精選"] = "TRUE"
        rows[idx] = new_row
        print(f"  ✓ {nm}：「{old_row['日文店名']}」→「{new_name}」⭐{place.get('rating', '-')}（{new_reviews}）")
        time.sleep(0.3)

    # ============================================================
    # Step 3: DOUBLECHECK 3 家
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 3：DOUBLECHECK 重核 3 家")
    print("=" * 60)
    if skip_api:
        print("  --skip-api 跳過")
    for item in (DOUBLECHECK if not skip_api else []):
        nm = item["name"]
        q = item["query"]
        old_substr = item["old_substr"]
        try:
            place = text_search_one(q)
        except Exception as e:
            print(f"  ❌ 「{nm}」query 失敗：{e}")
            continue
        m = find_row(rows, old_substr)
        if m is None:
            print(f"  ⚠ 找不到舊 row「{old_substr}」，略過")
            continue
        idx, old_row = m
        old_reviews = int(old_row.get("評論數") or 0)
        if not place:
            print(f"  ⚠ 「{nm}」新 query 也找不到京都府結果，保留舊資料 + 標需人工確認")
            old_row["兼類別"] = (old_row.get("兼類別") or "")  # noop
            old_row["__note"] = "需人工確認（新 query 無京都府結果）"
            continue
        new_reviews = place.get("userRatingCount") or 0
        new_name = (place.get("displayName") or {}).get("text", "")
        if new_reviews > old_reviews:
            cat = old_row["類別"]
            new_row = place_to_row(place, cat)
            new_row["精選"] = "TRUE"
            rows[idx] = new_row
            print(f"  ✓ {nm}：替換 {old_row['日文店名']}({old_reviews}) → {new_name}({new_reviews})")
        else:
            # 保留舊 row，但仍是 curated 名單成員 → 標精選；加 note
            old_row["精選"] = "TRUE"
            old_row["__note"] = f"需人工確認（新 query={new_name}, 評論 {new_reviews} ≤ 舊 {old_reviews}）"
            print(f"  · {nm}：保留 {old_row['日文店名']}({old_reviews})，新 query 結果 {new_name}({new_reviews}) 評論不多，標需人工確認")
        time.sleep(0.3)

    # 把 __note 寫進「兼類別」欄位（暫借用以記註，但更乾淨：另起 note 欄）
    # 改：把 note 併進「兼類別」會混淆語意。改寫成獨立 NOTE 欄；但 schema 已固定，
    # 簡單做法：把 note 接在「店家網站」尾端的 #note=... 太醜。
    # 折衷：寫進 cleaned/shopping_notes.txt 而非 csv 欄位。
    notes_path = BASE / "cleaned" / "shopping_notes.txt"
    with open(notes_path, "w", encoding="utf-8") as f:
        for r in rows:
            n = r.pop("__note", None)
            if n:
                f.write(f"[{r['類別']}] {r['日文店名']}（{get_cid(r.get('Google Maps連結', ''))}）：{n}\n")

    # ============================================================
    # Step 4: 標精選
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 4：標精選 TRUE")
    print("=" * 60)
    marked = 0
    for cat, names in CURATED_NAMES.items():
        for nm in names:
            target = ALIAS.get(nm, nm)
            m = find_row(rows, target)
            if m is None:
                print(f"  ⚠ 精選「{nm}」找不到對應 row")
                continue
            idx, r = m
            if r.get("精選") != "TRUE":
                r["精選"] = "TRUE"
                marked += 1
    # 額外補精選
    for substr in EXTRA_CURATED_SUBSTR:
        m = find_row(rows, substr)
        if m:
            idx, r = m
            if r.get("精選") != "TRUE":
                r["精選"] = "TRUE"
                marked += 1
                print(f"  ✓ 額外補精選：{r['日文店名']}")
    total_curated = sum(1 for r in rows if r.get("精選") == "TRUE")
    print(f"  本次新標 {marked} 個；總精選 {total_curated} 筆")

    # ============================================================
    # Step 5: 跨檔比對 all_restaurants.csv 找兼類別
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 5：跨檔 cid 比對標兼類別")
    print("=" * 60)
    if RESTAURANTS_CSV.exists():
        rest_cid_to_cat: dict[str, set[str]] = {}
        with open(RESTAURANTS_CSV, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                cid = get_cid(r.get("Google Maps連結", ""))
                if cid:
                    rest_cid_to_cat.setdefault(cid, set()).add(r.get("類別", ""))
        cross = 0
        for r in rows:
            cid = get_cid(r.get("Google Maps連結", ""))
            if not cid:
                continue
            cats = rest_cid_to_cat.get(cid, set())
            if cats:
                r["兼類別"] = ";".join(sorted(cats))
                cross += 1
                print(f"  ✓ {r['日文店名']}（{r['類別']}）兼 {r['兼類別']}")
        print(f"  跨檔重複 cid：{cross} 筆標兼類別")
    else:
        print(f"  ⚠ 找不到 {RESTAURANTS_CSV}，略過跨檔比對")

    # ============================================================
    # Step 6: dedup by cid（同 cid 留精選優先 / 評論多者）
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 6：dedup by cid（精選 > 評論多者勝出）")
    print("=" * 60)
    by_cid: dict[str, list[dict]] = {}
    no_cid: list[dict] = []
    for r in rows:
        cid = get_cid(r.get("Google Maps連結", ""))
        if cid:
            by_cid.setdefault(cid, []).append(r)
        else:
            no_cid.append(r)
    deduped: list[dict] = []
    dup_n = 0
    for cid, group in by_cid.items():
        if len(group) == 1:
            deduped.append(group[0])
            continue
        # 多列：精選優先、再評論多
        def _key(r: dict) -> tuple:
            is_curated = r.get("精選") == "TRUE"
            try:
                rv = int(r.get("評論數") or 0)
            except ValueError:
                rv = 0
            return (0 if is_curated else 1, -rv)
        group.sort(key=_key)
        winner = group[0]
        # 兼類別：把同 cid 其他 row 的類別也合進來
        extra_cats: set[str] = set()
        for other in group[1:]:
            if other["類別"] != winner["類別"]:
                extra_cats.add(other["類別"])
        if extra_cats:
            existing = set((winner.get("兼類別") or "").split(";")) - {""}
            existing |= extra_cats
            winner["兼類別"] = ";".join(sorted(existing))
        deduped.append(winner)
        dup_n += len(group) - 1
        print(f"  ✓ cid={cid}：保留「{winner['日文店名']}」({winner['類別']}, 精選={winner.get('精選') or '-'})，砍 {len(group)-1} 筆")
    deduped.extend(no_cid)
    print(f"  共 dedup {dup_n} 筆 → {len(deduped)} 列")

    rows = deduped

    # ============================================================
    # Step 7: 寫回 shopping.csv + data.js
    # ============================================================
    print("\n" + "=" * 60)
    print("Step 7：寫回 shopping.csv + data.js")
    print("=" * 60)
    with open(SHOPPING_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHOPPING_HEADERS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in SHOPPING_HEADERS})
    print(f"  📁 {SHOPPING_CSV}（{len(rows)} 列）")

    # 更新 data.js __SHOPPING_DATA
    data_js = BASE / "data.js"
    buf = io.StringIO()
    w2 = csv.DictWriter(buf, fieldnames=SHOPPING_HEADERS)
    w2.writeheader()
    for r in rows:
        w2.writerow({k: r.get(k, "") for k in SHOPPING_HEADERS})
    csv_text = buf.getvalue()
    if data_js.exists():
        existing = data_js.read_text(encoding="utf-8")
    else:
        existing = ""
    existing = re.sub(r"window\.__SHOPPING_DATA\s*=\s*.*?;\s*\n", "", existing, flags=re.S)
    new_block = "window.__SHOPPING_DATA = " + json.dumps(csv_text, ensure_ascii=False) + ";\n"
    data_js.write_text(existing + new_block, encoding="utf-8")
    print(f"  📁 {data_js}（已更新 __SHOPPING_DATA）")

    print(f"\n💰 實際 API call：{_TEXT_CALLS} 次 ≈ ${_TEXT_CALLS * 0.032:.2f} USD")
    print(f"📁 註記檔：{notes_path}（如有需人工確認列）")


if __name__ == "__main__":
    main()
