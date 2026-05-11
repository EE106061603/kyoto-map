#!/usr/bin/env python3
"""清理 all_restaurants.csv：

1. 用 cid 去除重複（同 cid 多個類別只留一個）
2. 砍掉地標 / 神社 / 寺廟 → 移到 attractions.csv
3. 砍掉車站 / 商場 / 純飯店 → 整列丟
4. 多類別重複時用名稱關鍵字硬規則 → 冷門度 → CATEGORIES 順序 挑主類別
5. 合併同 cid 列：日文店名取最長，店名做基本翻譯，其他欄位取最完整

Usage:
    python clean_csv.py --dry-run     # 只看影響面，不寫檔
    python clean_csv.py               # 真的寫 cleaned/ 目錄
    python clean_csv.py --apply       # 同上 + 覆蓋 all_restaurants.csv
"""

from __future__ import annotations
import csv
import json
import math
import re
import sys
import time
from pathlib import Path
from collections import Counter, defaultdict

# Windows utf-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = Path(__file__).resolve().parent
SRC = BASE / "all_restaurants.csv"
OUT = BASE / "cleaned"
OUT.mkdir(exist_ok=True)
OSM_CACHE = BASE / "osm_pois.json"

# CSV 欄位（與 collect.py 一致）
CSV_HEADERS = [
    "類別", "店名", "日文店名", "地址", "緯度", "經度",
    "直線距離（km）",
    "評分", "評論數", "價位等級", "營業時間",
    "交通時間（分鐘）", "步行時間（分鐘）", "換乘次數",
    "Google Maps連結", "店家網站", "照片資源",
]

# CATEGORIES 順序（tiebreak）
CATEGORIES_ORDER = [
    "鰻魚飯", "丼飯", "拉麵", "抹茶甜點", "燒烤", "居酒屋",
    "甜點", "章魚燒", "炸豬排", "飯糰專賣", "日式洋食",
    "日式早餐", "川床料理",
]

# 嚴格類別：collect.py 用廣搜詞所以噪音多。名稱沒命中對應關鍵字就不能贏。
STRICT_CATS = {
    "鰻魚飯":     ["うなぎ", "鰻", "ひつまぶし"],
    "章魚燒":     ["たこ", "蛸"],
    "飯糰專賣":   ["おにぎり", "おむすび"],
    "炸豬排":     ["とんかつ", "トンカツ", "豚カツ", "カツ", "勝牛"],
    "川床料理":   ["川床", "納涼床"],
}

# ============================================================
# 名稱關鍵字 → 主類別映射（按優先序排，越上面越優先）
# ============================================================
NAME_RULES = [
    ("川床料理",   ["川床", "納涼床", "鴨川 床"]),
    ("鰻魚飯",     ["うなぎ", "鰻", "ひつまぶし"]),
    ("章魚燒",     ["たこ焼", "たこやき", "蛸焼", "たこ八"]),
    ("飯糰專賣",   ["おにぎり", "おむすび", "握飯"]),
    ("拉麵",       ["ラーメン", "ラーメン店", "つけ麺", "拉麺", "らーめん"]),
    ("炸豬排",     ["とんかつ", "トンカツ", "豚カツ", "かつ亭", "かつ屋", "かつくら"]),
    ("抹茶甜點",   ["抹茶", "茶寮", "茶房", "甘味処"]),
    ("居酒屋",     ["居酒屋", "酒場", "横丁", "焼き鳥", "やきとり", "串焼", "串カツ"]),
    ("燒烤",       ["焼肉", "やきにく", "焼き肉", "ホルモン", "和牛"]),
    ("丼飯",       ["丼", "どんぶり", "親子丼", "海鮮丼", "天丼"]),
    ("日式早餐",   ["朝食", "モーニング", "和朝食"]),
    ("日式洋食",   ["洋食", "ハンバーグ", "オムライス"]),
    ("甜點",       ["パフェ", "スイーツ", "パンケーキ", "スフレ", "かき氷",
                    "ケーキ", "パティスリー", "シェ", "ガトー", "アイス"]),
]

# ============================================================
# 直接丟掉（不放入 attractions）
# ============================================================
# 名稱結尾「駅」「駅前」→ 車站
STATION_SUFFIX = re.compile(r"(駅|駅前|駅ビル|station|Station)$")

# 商場 / 購物中心
MALL_KEYWORDS = [
    "イオンモール", "イオンスタイル", "イオンタウン", "イオンシネマ", "イオン洛南",
    "BiVi", "ラゾーナ", "アウトレット",
    "イズミヤ", "アル・プラザ", "亀岡ショッピング",
    "MOMOテラス", "タワーテラス",
    "京都髙島屋", "京都高島屋", "タカシマヤ", "Takashimaya",
    "京都ヨドバシ", "京都ポルタ", "ヨドバシ",
    "京都駅ビル", "JR京都駅 ",
]

# 飯店連鎖 / 類關鍵字 — 用 regex 避免短縮寫（APA/ANA）誤命中 JAPANESE/OHANA
HOTEL_PAT = re.compile(
    r"(ホテル|HOTEL|Hotel|hotel|"
    r"旅館|料亭旅館|"
    r"\bInn\b|\bINN\b|"           # Inn 整詞才算（避免 OHANA 等誤）
    r"\bResort\b|\bRESORT\b|"
    r"ハイアット|Hyatt|HYATT|"
    r"Marriott|MARRIOTT|マリオット|"
    r"Hilton|HILTON|ヒルトン|"
    r"Sheraton|SHERATON|シェラトン|"
    r"Ritz|RITZ|リッツ|"
    r"Prince|PRINCE|プリンス|"
    r"インターコンチ|"
    r"MIMARU|ミマル|"
    r"三井ガーデン|Mitsui Garden|"
    r"ROKU\b|"
    r"ノク京都|"
    r"都ホテル|京都ホテル|"
    r"ザ・ホテル|"
    r"Dormy|ドーミーイン|"
    r"東横イン|TOYOKO|"
    r"アパホテル|\bAPA\b|"          # APA 整詞或 APAホテル
    r"アーバンホテル|"
    r"ビジネスホテル|"
    r"リッチモンド|"
    r"グランヴィア|"
    r"ロイヤルパーク|"
    r"\bSPA\b|温泉)"
)

# ============================================================
# 移到 attractions.csv（神社/寺廟/觀光景點）
# ============================================================
# 名稱含寺廟字 → 寺廟候選（堂$ 加進來分類用，但需配合餐廳關鍵字排除）
TEMPLE_NAME_PAT = re.compile(r"(寺$|寺院$|院$|堂$|大師$|本山$|分院$|禅寺$|塔頭$)")
# 名稱含神社字 → 神社候選（包含後接 ）/) 的版本，例：「賀茂御祖神社（下鴨神社）」）
SHRINE_NAME_PAT = re.compile(r"(神社[)）]?$|神宮[)）]?$|大社[)）]?$|稲荷大社$|八幡宮$|天満宮$)")

# 強地標 suffix（幾乎一定是地標）
HARD_LANDMARK_PAT = re.compile(
    r"(寺$|寺院$|神社[)）]?$|神宮[)）]?$|大社[)）]?$|天満宮$|稲荷大社$|八幡宮$|宮殿$|城$)"
)
# 弱地標 suffix（要配合 OSM + 評論 + 沒有餐廳關鍵字才算）
SOFT_LANDMARK_PAT = re.compile(
    r"(院$|堂$|閣$|苑$|殿$|塔$|廟$|門$|橋$|跡$|園$)"
)

# 餐廳關鍵字（任何字在名字中出現就排除地標判定）
RESTAURANT_KW_PAT = re.compile(
    r"(食堂|茶房|茶寮|喫茶|料理|料庭|餐廳|レストラン|Restaurant|RESTAURANT|"
    r"カフェ|cafe|Cafe|CAFE|coffee|COFFEE|Coffee|"
    r"ダイニング|Dining|DINING|"
    r"居酒屋|居酒家|居酒|酒場|Bar|BAR|tapas|"
    r"ビストロ|Bistro|BISTRO|"
    r"とんかつ|トンカツ|豚カツ|カツ亭|"
    r"ラーメン|らーめん|麺|つけ麺|拉麺|"
    r"うどん|そば|蕎麦|"
    r"寿司|すし|鮨|"
    r"すき焼き|しゃぶしゃぶ|和牛|焼肉|焼き肉|やきにく|"
    r"焼鳥|焼き鳥|やきとり|串焼|串カツ|ホルモン|"
    r"たこ焼|たこやき|たこ八|蛸焼|"
    r"うなぎ|鰻|ひつまぶし|"
    r"おにぎり|おむすび|"
    r"スイーツ|パフェ|ケーキ|プリン|もなか|煎餅|団子|和菓子|"
    r"くれぇぷ|アイス|チョコ|タルト|キッシュ|"
    r"洋食|ハンバーグ|オムライス|カレー|"
    r"朝食|モーニング|和朝食|"
    r"ベーカリー|パン|サンド|工房|キッチン|kitchen|Kitchen|"
    r"ぽん酢|割烹|京菓子|京料理|京豆|"
    r"鶏|豚|牛|肉|"
    r"丼|どんぶり|"
    r"ソフトクリーム|ジェラート|gelato|"
    r"バー|スナック|"
    r"ピザ|Pizza|パスタ|burger|grill|Grill|GRILL|"
    r"川床|納涼床|"
    r"ヴィレッジ|ビレッジ|Village|"
    r"屋$|店$|"   # 結尾「屋」「店」幾乎就是餐廳
    r"chicken|Chicken|CHICKEN|"
    r"tacos|Tacos|TACOS|タコス|"
    r"sushi|SUSHI|"
    r"diner|Diner|DINER|"
    r"BBQ|bbq|"
    r"ニュー|Neo|NEO|New|NEW|"   # 「ニュー東寺」這種 New X 開頭餐廳
    r"アフレ|Apple|"  # 西院附近店家
    r"鶏|肉|魚|エビ|"
    r"屋台|"
    r"ファクトリー|Factory|"
    r"オムレツ|"
    r"ホットサンド|"
    r"スタンド|stand|Stand|STAND)"
)

# 政府 / 公共機關 domain
GOV_DOMAIN_PAT = re.compile(r"\.(lg|go)\.jp(/|$)")

# OSM 半徑門檻（放大到 100m，因為大寺廟主建築離 OSM 標記點常 50-150m）
OSM_RADIUS_M = 100

# ============================================================
# 翻譯字典：日文 → 中譯（substring 替換，越長越先）
# ============================================================
TRANS_DICT = {
    # 麵類
    "ラーメン": "拉麵", "らーめん": "拉麵", "拉麺": "拉麵",
    "つけ麺": "沾麵", "担々麺": "擔擔麵", "うどん": "烏龍麵", "そば": "蕎麥麵",
    # 鰻魚
    "うなぎ": "鰻魚", "ひつまぶし": "鰻魚飯三吃", "鰻": "鰻",
    # 燒烤
    "焼肉": "燒肉", "焼き肉": "燒肉", "やきにく": "燒肉",
    "焼鳥": "燒鳥", "焼き鳥": "燒鳥", "やきとり": "燒鳥",
    "串焼き": "串燒", "串カツ": "串炸", "ホルモン": "內臟燒烤", "和牛": "和牛",
    # 飯糰
    "おにぎり": "飯糰", "おむすび": "飯糰",
    # 章魚燒
    "たこ焼き": "章魚燒", "たこやき": "章魚燒", "蛸焼": "章魚燒",
    # 炸豬排
    "とんかつ": "炸豬排", "トンカツ": "炸豬排", "豚カツ": "炸豬排",
    # 抹茶 / 甜點
    "抹茶": "抹茶", "茶寮": "茶寮", "茶房": "茶房",
    "スイーツ": "甜點", "パフェ": "聖代", "ケーキ": "蛋糕",
    "パンケーキ": "鬆餅", "スフレ": "舒芙蕾", "かき氷": "刨冰",
    "アイス": "冰品", "ジェラート": "義式冰淇淋", "プリン": "布丁",
    "シュークリーム": "泡芙", "パティスリー": "甜點店",
    # 居酒屋類
    "居酒屋": "居酒屋", "酒場": "酒場", "横丁": "橫丁",
    # 丼飯
    "丼": "丼", "どんぶり": "丼", "親子丼": "親子丼", "海鮮丼": "海鮮丼",
    "天丼": "天丼", "カツ丼": "豬排丼",
    # 川床
    "川床": "川床", "納涼床": "納涼床",
    # 早餐
    "朝食": "早餐", "モーニング": "早餐", "和朝食": "和式早餐",
    # 洋食
    "洋食": "洋食", "ハンバーグ": "漢堡排", "オムライス": "蛋包飯",
    "カレー": "咖哩", "ピザ": "披薩", "パスタ": "義大利麵",
    # 通用詞
    "本店": "本店", "支店": "分店", "別館": "別館", "新店": "新店",
    "京都駅前": "京都站前", "駅前": "站前", "京都": "京都",
    "コーヒー": "咖啡", "カフェ": "咖啡店", "喫茶": "喫茶",
    "レストラン": "餐廳", "食堂": "食堂", "屋": "屋",
    "ホテル": "飯店", "旅館": "旅館",
    # 店家常用後綴
    "の店": "之店", "屋": "屋",
}


def translate_name(ja: str) -> str:
    """基本日文 → 中文翻譯。漢字部分保留，假名部分按字典替換。
    沒命中的假名段保留原樣。
    """
    if not ja:
        return ja
    out = ja
    # 按詞長度由長到短做替換（避免「焼肉」被「肉」先取代）
    for src, dst in sorted(TRANS_DICT.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(src, dst)
    return out


# ============================================================
# OSM POI 載入（cache）
# ============================================================
def load_osm_pois() -> list[dict]:
    """讀 osm_pois.json；若不存在則用 Overpass API 抓並存 cache。
    回傳 list of {lat, lng, name, type=shrine|temple|attr}
    """
    if OSM_CACHE.exists():
        with open(OSM_CACHE, encoding="utf-8") as f:
            return json.load(f)

    print("📡 抓 OSM POI（Overpass API）...")
    import requests
    bbox = "34.4,135.18,35.2,135.85"  # 京都 + 大阪 + 奈良邊界
    query = f"""[out:json][timeout:60];
(
  node["tourism"="attraction"]({bbox});
  node["historic"~"shrine|temple|monastery"]({bbox});
  node["amenity"="place_of_worship"]["religion"~"shinto|buddhist"]({bbox});
);
out body;"""
    url = "https://overpass-api.de/api/interpreter"
    r = requests.post(
        url, data={"data": query},
        headers={"User-Agent": "kyoto-trip-cleanup/1.0"},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    pois = []
    for e in data.get("elements", []):
        tags = e.get("tags") or {}
        if tags.get("religion") == "shinto" or tags.get("historic") == "shrine":
            t = "shrine"
        elif (tags.get("religion") == "buddhist"
              or tags.get("historic") in ("temple", "monastery")):
            t = "temple"
        else:
            t = "attr"
        pois.append({
            "lat": e["lat"], "lng": e["lon"],
            "name": tags.get("name") or tags.get("name:en") or "",
            "type": t,
        })
    with open(OSM_CACHE, "w", encoding="utf-8") as f:
        json.dump(pois, f, ensure_ascii=False)
    print(f"   存 {len(pois)} 個 POI 到 {OSM_CACHE.name}")
    return pois


# ============================================================
# 工具
# ============================================================
def haversine_m(lat1, lng1, lat2, lng2):
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
           * math.sin(dlng/2)**2)
    return 2 * R * math.asin(math.sqrt(a))


def extract_cid(url: str) -> str:
    if not url:
        return ""
    m = re.search(r"cid=(\d+)", url)
    return m.group(1) if m else ""


def find_nearby_osm(lat, lng, pois, radius_m=OSM_RADIUS_M):
    """回傳 (osm_type, osm_name) 若在半徑內，否則 None"""
    best = None
    best_d = radius_m + 1
    for p in pois:
        d = haversine_m(lat, lng, p["lat"], p["lng"])
        if d < best_d:
            best_d = d
            best = p
    return best


# ============================================================
# 分類器
# ============================================================
def is_station(name: str) -> bool:
    if not name or has_restaurant_kw(name):
        return False
    return bool(STATION_SUFFIX.search(name))


def is_mall(name: str) -> bool:
    """商場 = 名稱以 mall 關鍵字開頭（避免「サイゼリヤ イオンモール」等真餐廳誤殺）"""
    if not name:
        return False
    return any(name.startswith(kw) for kw in MALL_KEYWORDS)


def is_hotel_pure(name: str, price: str) -> bool:
    """名稱命中飯店 regex + priceLevel 空 + 不是餐廳 → 純飯店"""
    if not name or has_restaurant_kw(name):
        return False
    return bool(HOTEL_PAT.search(name)) and not (price or "").strip()


def is_gov_domain(website: str) -> bool:
    if not website:
        return False
    return bool(GOV_DOMAIN_PAT.search(website))


def classify_attraction(name: str, addr: str, osm_match: dict | None) -> str:
    """回傳 神社 / 寺廟 / 觀光景點"""
    n = name or ""
    a = addr or ""
    # 名稱優先（最直接）
    if SHRINE_NAME_PAT.search(n):
        return "神社"
    if TEMPLE_NAME_PAT.search(n):
        return "寺廟"
    # 再看 OSM tag
    if osm_match:
        if osm_match["type"] == "shrine":
            return "神社"
        if osm_match["type"] == "temple":
            return "寺廟"
    # 從地址退而求其次
    if SHRINE_NAME_PAT.search(a):
        return "神社"
    if TEMPLE_NAME_PAT.search(a):
        return "寺廟"
    return "觀光景點"


def has_restaurant_kw(name: str) -> bool:
    """名稱中是否含餐廳關鍵字 — 含則不是地標"""
    return bool(RESTAURANT_KW_PAT.search(name or ""))


def is_hard_landmark_name(name: str) -> bool:
    """強地標：寺/神社/神宮/大社/...結尾，且不含餐廳關鍵字"""
    if not name or has_restaurant_kw(name):
        return False
    return bool(HARD_LANDMARK_PAT.search(name))


def is_soft_landmark_name(name: str) -> bool:
    """弱地標：院/堂/閣/...結尾，且不含餐廳關鍵字（要再配 OSM + 評論驗證）"""
    if not name or has_restaurant_kw(name):
        return False
    return bool(SOFT_LANDMARK_PAT.search(name))


def classify_row(row: dict, osm_pois: list) -> tuple[str, str | None]:
    """回傳 (action, reason):
       action ∈ {drop_station, drop_mall, drop_hotel, attraction, restaurant}
       reason 是更詳細的原因說明
    """
    # 從合回的 attractions.csv 來的列：直接判 attraction，不再走餐廳分類器
    if row.get("類別") in ("神社", "寺廟", "觀光景點"):
        return ("attraction", f"先前已分類為 {row['類別']}")
    name = row.get("日文店名") or row.get("店名") or ""
    addr = row.get("地址") or ""
    price = row.get("價位等級") or ""
    website = row.get("店家網站") or ""
    reviews_str = row.get("評論數") or "0"
    try:
        reviews = int(reviews_str) if reviews_str else 0
    except ValueError:
        reviews = 0

    # ---- 直接丟（不入 attractions）----
    if is_station(name):
        return ("drop_station", f"名稱結尾為車站：{name}")
    if is_mall(name):
        return ("drop_mall", f"商場關鍵字：{name}")
    if is_hotel_pure(name, price):
        return ("drop_hotel", f"飯店且無 priceLevel：{name}")

    # ---- 移到 attractions ----
    try:
        lat = float(row.get("緯度") or 0)
        lng = float(row.get("經度") or 0)
    except ValueError:
        lat = lng = 0
    osm_match = None
    if lat and lng:
        osm_match = find_nearby_osm(lat, lng, osm_pois)

    has_restaurant = has_restaurant_kw(name)

    # 規則 1：強地標 suffix（寺/神社/神宮/大社/...）→ 直接判
    if is_hard_landmark_name(name):
        cat = classify_attraction(name, addr, osm_match)
        return ("attraction",
                f"{cat}（強地標名稱結尾：{name}）")
    # 規則 2：弱地標 suffix（院/堂/閣/...）+ OSM 內有寺廟/神社 + 評論 > 200
    #         + priceLevel 空 + 不含餐廳關鍵字
    if (is_soft_landmark_name(name) and osm_match
            and osm_match["type"] in ("temple", "shrine")
            and reviews > 200 and not price.strip()):
        cat = classify_attraction(name, addr, osm_match)
        return ("attraction",
                f"{cat}（弱地標 + OSM {OSM_RADIUS_M}m 內 {osm_match['type']} + 評論 {reviews}）")
    # 規則 3：評論 > 10000 + priceLevel 空 + 不含餐廳關鍵字（撈漏網大景點）
    if reviews > 10000 and not price.strip() and not has_restaurant:
        cat = classify_attraction(name, addr, osm_match)
        return ("attraction",
                f"{cat}（評論 {reviews} > 10000 + 無 priceLevel + 不像餐廳）")
    # 規則 4：政府 domain
    if is_gov_domain(website):
        cat = classify_attraction(name, addr, osm_match)
        return ("attraction", f"{cat}（政府網域 {website}）")
    # 規則 5：評論數 = 0 + 名稱不含餐廳關鍵字（避免新店誤殺）
    if reviews == 0 and not has_restaurant:
        cat = classify_attraction(name, addr, osm_match)
        return ("attraction", f"{cat}（評論數 = 0 + 不像餐廳）")

    return ("restaurant", None)


# ============================================================
# 多類別重複的店家挑主類別
# ============================================================
def pick_winning_category(rows: list[dict], category_counts: Counter) -> tuple[str, str]:
    """rows 是同一個 cid 出現過的所有列。回傳 (winning_category, reason)
       category_counts: 全資料中各類別出現的「unique cid 計數」（用於冷門度）
    """
    cats_in = set(r["類別"] for r in rows)
    name = max((r["日文店名"] or r["店名"] or "" for r in rows), key=len)

    # 規則 1：店名命中關鍵字
    for cat, kws in NAME_RULES:
        if cat in cats_in:
            for kw in kws:
                if kw in name:
                    return (cat, f"店名「{name}」命中關鍵字「{kw}」")

    # 規則 2：嚴格類別過濾 — 名稱沒命中對應關鍵字就不能贏
    eligible = set(cats_in)
    for cat, kws in STRICT_CATS.items():
        if cat in eligible and not any(kw in name for kw in kws):
            eligible.discard(cat)
    # 全部都被嚴格過濾掉 → 只能從原候選挑（罕見 fallback）
    if not eligible:
        eligible = cats_in

    # 規則 3：冷門度（eligible 候選中最少 unique cid 的那個）
    def _order_idx(c):
        try:
            return CATEGORIES_ORDER.index(c)
        except ValueError:
            return 999  # 非餐廳類別（如「寺廟」）排最後
    if len(eligible) > 1:
        sorted_cats = sorted(eligible, key=lambda c: (category_counts.get(c, 0),
                                                     _order_idx(c)))
        excluded = cats_in - eligible
        suffix = f"（嚴格排除 {sorted(excluded)}）" if excluded else ""
        return (sorted_cats[0],
                f"冷門度（{sorted_cats[0]}={category_counts.get(sorted_cats[0], 0)}）{suffix}")

    return (next(iter(eligible)),
            f"唯一候選（嚴格排除 {sorted(cats_in - eligible)}）" if cats_in - eligible else "唯一類別")


# ============================================================
# 合併同 cid 多列
# ============================================================
def pick_field(rows: list[dict], field: str) -> str:
    """從多列中挑非空且最長的值"""
    vals = [(r.get(field) or "").strip() for r in rows]
    nonempty = [v for v in vals if v]
    if not nonempty:
        return ""
    return max(nonempty, key=len)


def merge_rows(rows: list[dict], chosen_category: str) -> dict:
    """合併同 cid 多列為一筆"""
    # 日文店名取最長
    ja = pick_field(rows, "日文店名")
    # 店名根據最長日文店名翻譯
    zh = translate_name(ja)
    merged = dict(rows[0])  # 起點
    merged["類別"] = chosen_category
    merged["店名"] = zh
    merged["日文店名"] = ja
    # 其他欄位取最完整（最長非空）
    for f in CSV_HEADERS:
        if f in ("類別", "店名", "日文店名"):
            continue
        merged[f] = pick_field(rows, f)
    return merged


# ============================================================
# 主流程
# ============================================================
def main(*, dry_run: bool = False, apply_overwrite: bool = False):
    print("=" * 60)
    print("📂 載入 all_restaurants.csv")
    with open(SRC, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"   原始 {len(rows)} 列")
    # 也把先前分離的 attractions 合回來重新分類（保持冪等）
    attr_csv = OUT / "attractions.csv"
    if attr_csv.exists():
        with open(attr_csv, encoding="utf-8-sig") as f:
            prev_attr = list(csv.DictReader(f))
        if prev_attr:
            rows.extend(prev_attr)
            print(f"   合回先前景點 {len(prev_attr)} 列")

    print("=" * 60)
    osm_pois = load_osm_pois()
    print(f"   OSM POI: {len(osm_pois)}")

    # 把每列分類（依 cid 群組，但分類用「組內任一列」結果）
    print("=" * 60)
    print("🔍 分類 + 去重（依 cid）...")

    # 1. 依 cid 分組
    groups: dict[str, list[dict]] = defaultdict(list)
    no_cid_rows: list[dict] = []
    for r in rows:
        cid = extract_cid(r.get("Google Maps連結") or "")
        if cid:
            groups[cid].append(r)
        else:
            no_cid_rows.append(r)
    print(f"   {len(groups)} 個 unique cid + {len(no_cid_rows)} 列無 cid")

    # 2. 對每個 cid 做 classify_row（用組內第一列代表，欄位通常一致）
    drop_station = []
    drop_mall = []
    drop_hotel = []
    attractions: list[dict] = []
    restaurants: list[dict] = []   # tuple (cid, group_rows, action_reason)
    rest_groups: dict[str, list[dict]] = {}

    for cid, grp in groups.items():
        # 用第一列做分類（同 cid 列差異主要在「類別」欄）
        action, reason = classify_row(grp[0], osm_pois)

        if action == "drop_station":
            drop_station.append((cid, grp, reason))
        elif action == "drop_mall":
            drop_mall.append((cid, grp, reason))
        elif action == "drop_hotel":
            drop_hotel.append((cid, grp, reason))
        elif action == "attraction":
            # attractions 也合併同 cid 列（取一筆代表）
            cat = classify_attraction(
                grp[0].get("日文店名", ""),
                grp[0].get("地址", ""),
                find_nearby_osm(
                    float(grp[0].get("緯度") or 0),
                    float(grp[0].get("經度") or 0),
                    osm_pois,
                ),
            )
            merged = merge_rows(grp, cat)
            attractions.append(merged)
        else:
            rest_groups[cid] = grp

    # 3. 餐廳組：對每個 cid 挑主類別 + 合併
    # 計算「全資料各類別 unique cid 數」當冷門度依據
    cat_unique_cid = Counter()
    for cid, grp in rest_groups.items():
        cats_in = set(r["類別"] for r in grp)
        for c in cats_in:
            cat_unique_cid[c] += 1

    multi_cat_examples = []   # 用來 dry-run 報告多類別樣本
    for cid, grp in rest_groups.items():
        cat, reason = pick_winning_category(grp, cat_unique_cid)
        merged = merge_rows(grp, cat)
        restaurants.append(merged)
        if len(set(r["類別"] for r in grp)) > 1:
            multi_cat_examples.append((merged["日文店名"],
                                       sorted(set(r["類別"] for r in grp)),
                                       cat, reason))

    # ============================================================
    # 報告
    # ============================================================
    print("=" * 60)
    print("📊 結果")
    print(f"   ✅ 餐廳（清理後 unique 列）: {len(restaurants)}")
    print(f"   ⛩  景點（神社/寺廟/觀光）  : {len(attractions)}")
    print(f"   ❌ 砍掉車站                : {len(drop_station)} cid")
    print(f"   ❌ 砍掉商場                : {len(drop_mall)} cid")
    print(f"   ❌ 砍掉飯店                : {len(drop_hotel)} cid")
    print(f"   ❓ 無 cid 列（保留原樣）   : {len(no_cid_rows)}")
    print()

    # 景點分類分佈
    attr_breakdown = Counter(a["類別"] for a in attractions)
    print(f"   景點分類: {dict(attr_breakdown)}")

    # 餐廳分類分佈
    rest_breakdown = Counter(r["類別"] for r in restaurants)
    print(f"   餐廳分類:")
    for cat in CATEGORIES_ORDER:
        n = rest_breakdown.get(cat, 0)
        print(f"     {cat}: {n}")
    print()

    # ============================================================
    # 寫 preview / 實際輸出
    # ============================================================
    target = OUT
    print(f"💾 輸出目錄: {target}")

    # cleaned restaurants
    with open(target / "cleaned_restaurants.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        w.writerows(restaurants)

    # attractions
    with open(target / "attractions.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        w.writerows(attractions)

    # dropped hotels (給用戶 review)
    with open(target / "dropped_hotels.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write("﻿cid,日文店名,地址,評分,評論數,價位,網站,原因\n")
        for cid, grp, reason in drop_hotel:
            r = grp[0]
            f.write(",".join([
                cid, r["日文店名"], (r["地址"] or "").replace(",", "，"),
                r.get("評分", ""), r.get("評論數", ""),
                r.get("價位等級", ""), r.get("店家網站", ""),
                reason.replace(",", "，"),
            ]) + "\n")

    # dropped stations + malls
    with open(target / "dropped_stations.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write("﻿cid,日文店名,地址,原因\n")
        for cid, grp, reason in drop_station:
            r = grp[0]
            f.write(",".join([cid, r["日文店名"],
                              (r["地址"] or "").replace(",", "，"), reason]) + "\n")
    with open(target / "dropped_malls.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write("﻿cid,日文店名,地址,原因\n")
        for cid, grp, reason in drop_mall:
            r = grp[0]
            f.write(",".join([cid, r["日文店名"],
                              (r["地址"] or "").replace(",", "，"), reason]) + "\n")

    # report
    with open(target / "cleanup_report.txt", "w", encoding="utf-8") as f:
        f.write("# 清理報告\n\n")
        f.write(f"原始: {len(rows)} 列 / {len(groups)} unique cid\n\n")
        f.write(f"清理後餐廳: {len(restaurants)} 列\n")
        f.write(f"景點: {len(attractions)} 列\n")
        f.write(f"砍車站: {len(drop_station)} cid\n")
        f.write(f"砍商場: {len(drop_mall)} cid\n")
        f.write(f"砍飯店: {len(drop_hotel)} cid\n")
        f.write(f"無 cid: {len(no_cid_rows)} 列\n\n")
        f.write("## 景點分類分佈\n")
        for c, n in attr_breakdown.items():
            f.write(f"  {c}: {n}\n")
        f.write("\n## 餐廳分類分佈\n")
        for cat in CATEGORIES_ORDER:
            f.write(f"  {cat}: {rest_breakdown.get(cat, 0)}\n")
        f.write("\n## 多類別重複樣本（前 50）\n")
        for name, orig_cats, won, reason in multi_cat_examples[:50]:
            f.write(f"  {name}\n")
            f.write(f"    原本類別: {orig_cats}\n")
            f.write(f"    判給: {won}（{reason}）\n")
        f.write("\n## 砍掉的飯店（請 review）\n")
        for cid, grp, reason in drop_hotel[:80]:
            r = grp[0]
            f.write(f"  - {r['日文店名']}（{reason}）\n")

    print(f"\n✅ 完成！查看 {target}/cleanup_report.txt 看摘要")
    if dry_run:
        print("   --dry-run 模式，未動到 all_restaurants.csv")
    elif apply_overwrite:
        print("\n⚠ --apply 啟用，覆寫 all_restaurants.csv...")
        with open(SRC, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            w.writeheader()
            w.writerows(restaurants)
        print(f"   已覆寫 → {SRC}")
        # 也順便重生 data.js（含餐廳 + 景點兩份 CSV，供 map.html 雙擊讀取）
        import io, json as _j

        def _csv_text(items):
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=CSV_HEADERS)
            w.writeheader()
            w.writerows(items)
            return buf.getvalue()

        data_js = BASE / "data.js"
        with open(data_js, "w", encoding="utf-8") as f:
            f.write("window.__CSV_DATA = "
                    + _j.dumps(_csv_text(restaurants), ensure_ascii=False) + ";\n")
            f.write("window.__ATTRACTIONS_DATA = "
                    + _j.dumps(_csv_text(attractions), ensure_ascii=False) + ";\n")
        print(f"   已重生 → {data_js}（含 {len(attractions)} 筆景點）")
    else:
        print(f"   要覆寫 all_restaurants.csv 請加 --apply")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(
        dry_run="--dry-run" in args,
        apply_overwrite="--apply" in args,
    )
