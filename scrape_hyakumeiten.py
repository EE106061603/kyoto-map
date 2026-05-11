"""
從 award.tabelog.com 抓食べログ百名店歷年京都得獎店家。
覆蓋 2021-2026、WEST + 全國類別。

輸出: cleaned/hyakumeiten_kyoto.json
格式:
{
  "26023737": {
    "name": "あいつのラーメン かたぐるま 本店",
    "url": "https://tabelog.com/kyoto/A2601/A260203/26023737/",
    "awards": [
      {"slug": "ramen_west", "label": "ラーメン WEST", "year": 2025},
      ...
    ]
  },
  ...
}
"""
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")

# slug → 顯示用標籤
CATEGORIES = {
    # WEST 類別（京都會出現的）
    "ramen_west": "拉麵",
    "yakitori_west": "焼鳥",
    "yakiniku_west": "焼肉",
    "izakaya_west": "居酒屋",
    "sweets_west": "甜點",
    "wagashi_west": "和菓子",
    "yoshoku_west": "洋食",
    "soba_west": "蕎麥",
    "udon_west": "烏龍麵",
    "japanese_west": "日本料理",
    "sushi_west": "壽司",
    "chinese_west": "中國料理",
    "cafe_west": "咖啡店",
    "bread_west": "麵包",
    "steak_west": "牛排・鐵板燒",
    "french_west": "法式",
    "italian_west": "義式",
    "asia_ethnic_west": "亞洲・民族料理",
    "curry_west": "咖哩",
    # 全國類別
    "unagi": "鰻魚",
    "tonkatsu": "炸豬排",
    "okonomiyaki": "大阪燒・章魚燒",
    "shokudo": "食堂",
    "gyoza": "餃子",
    "tempura": "天婦羅",
    "pizza": "披薩",
    "hamburger": "漢堡",
    "sukiyaki_shabushabu": "壽喜燒・涮涮鍋",
    "kissaten": "喫茶店",
    "bar": "酒吧",
    "ice_gelato": "冰品・義式冰淇淋",
    "creative_innovative": "創作料理",
    "spanish": "西班牙料理",
    "toriryori": "雞料理",
    "tachinomi": "立飲店",
}

YEARS = [2021, 2022, 2023, 2024, 2025, 2026]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}

# 提取單家店的 anchor 區塊（hyakumeiten-shop__target ...）
SHOP_BLOCK_RE = re.compile(
    r'<a class="hyakumeiten-shop__target[^"]*"[^>]*?'
    r'data-prop18-val="[^"]*?_(\d+)"[^>]*?'
    r'href="(https://tabelog\.com/kyoto/[^"]+)".*?'
    r'<div class="hyakumeiten-shop__name">\s*(.+?)\s*</div>',
    re.S,
)


def fetch_one(slug: str, year: int) -> list[tuple[str, str, str]]:
    """回傳 [(rst_id, name, url), ...]，URL 路徑含 /kyoto/ 才算京都府的店。"""
    url = f"https://award.tabelog.com/hyakumeiten/{slug}/{year}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException as exc:
        print(f"  [err] {slug} {year}: {exc}", file=sys.stderr)
        return []
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        print(f"  [skip] {slug} {year} HTTP {r.status_code}", file=sys.stderr)
        return []
    matches = SHOP_BLOCK_RE.findall(r.text)
    return [(rid, name.strip(), u) for rid, u, name in matches]


def main() -> None:
    results: dict[str, dict] = {}

    for slug, label in CATEGORIES.items():
        for year in YEARS:
            shops = fetch_one(slug, year)
            print(f"{slug} {year} → {len(shops)} 京都店")
            for rid, name, url in shops:
                entry = results.setdefault(
                    rid,
                    {"name": name, "url": url, "awards": []},
                )
                # 若同 rst_id 之前已記錄但這次名字更長 / 更乾淨，更新
                if len(name) > len(entry["name"]):
                    entry["name"] = name
                entry["awards"].append({"slug": slug, "label": label, "year": year})
            time.sleep(0.5)  # 對伺服器友善

    # 每店得獎紀錄按 (slug, year) 排序
    for entry in results.values():
        entry["awards"].sort(key=lambda a: (a["slug"], a["year"]))

    out_path = Path("cleaned") / "hyakumeiten_kyoto.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n寫入 {out_path}: {len(results)} 家京都店家")

    # 統計
    award_count = sum(len(e["awards"]) for e in results.values())
    print(f"總得獎紀錄: {award_count}")
    by_cat: dict[str, int] = {}
    for e in results.values():
        for a in e["awards"]:
            by_cat[a["label"]] = by_cat.get(a["label"], 0) + 1
    print("\n各類別得獎次數:")
    for label, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {label}: {n}")


if __name__ == "__main__":
    main()
