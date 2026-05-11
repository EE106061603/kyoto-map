"""
把 cleaned/hyakumeiten_kyoto.json 跟 all_restaurants.csv 比對，
找出 csv 裡的店家對應到哪些百名店得獎紀錄。

輸出:
- cleaned/hyakumeiten_matched.json  → {cid: {name, awards: [...]}}
- cleaned/hyakumeiten_unmatched.txt → 未對上的百名店條目（給人工 review）
"""
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# 移除常見尾綴，避免「本家 第一旭 本店」vs「第一旭本店」對不上
SUFFIX_TRIM = re.compile(
    r"(\s*[\(（][^\)）]+[\)）]\s*)+$"  # 括號內備註
)
TAIL_WORDS = [
    "総本店",
    "総本舗",
    "本店",
    "本舗",
    "店",
    "別館",
    "離れ",
]
HEAD_WORDS = [
    "本家",
    "元祖",
    "京都",
    "祇園",
    "京",
]


def normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = SUFFIX_TRIM.sub("", s)
    s = s.replace(" ", "").replace("　", "").replace("\t", "")
    s = s.lower()
    return s


def strip_decor(s: str) -> str:
    """更激進：去掉開頭/結尾常見詞，讓「本家 第一旭 本店」可以對到「第一旭」。"""
    core = normalize(s)
    changed = True
    while changed:
        changed = False
        for w in TAIL_WORDS:
            if core.endswith(w.lower()) and len(core) > len(w):
                core = core[: -len(w)]
                changed = True
        for w in HEAD_WORDS:
            if core.startswith(w.lower()) and len(core) > len(w):
                core = core[len(w) :]
                changed = True
    # 去掉分店字眼: 「祇園八坂店」「四条店」「京都駅前店」
    core = re.sub(r"(京都駅|京都|祇園|四条|河原町|烏丸|二条|三条|出町柳|嵐山|清水|北山|一乗寺|高雄|宇治).*?店$", "", core)
    return core


def main() -> None:
    # 載入 csv → 建索引
    csv_rows: list[dict] = []
    with open("all_restaurants.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row = {k.lstrip("﻿"): v for k, v in row.items()}
            csv_rows.append(row)

    # 用 cid 當 key（Google Maps 連結尾 cid）
    def cid_of(row: dict) -> str:
        url = row.get("Google Maps連結", "")
        m = re.search(r"cid=(\d+)", url)
        return m.group(1) if m else ""

    # 多重索引：normalized 日文店名 / normalized 中文店名 / decor stripped
    idx_jp: dict[str, list[dict]] = defaultdict(list)
    idx_zh: dict[str, list[dict]] = defaultdict(list)
    idx_jp_decor: dict[str, list[dict]] = defaultdict(list)
    idx_zh_decor: dict[str, list[dict]] = defaultdict(list)
    for row in csv_rows:
        jp = normalize(row.get("日文店名", ""))
        zh = normalize(row.get("店名", ""))
        if jp:
            idx_jp[jp].append(row)
            idx_jp_decor[strip_decor(row.get("日文店名", ""))].append(row)
        if zh:
            idx_zh[zh].append(row)
            idx_zh_decor[strip_decor(row.get("店名", ""))].append(row)

    # 載入百名店
    raw = json.loads(
        Path("cleaned/hyakumeiten_kyoto.json").read_text(encoding="utf-8")
    )

    # 預先把每 csv row 的 (decor jp, decor zh) 算好
    decor_pairs: list[tuple[dict, str, str]] = []
    for row in csv_rows:
        decor_pairs.append(
            (
                row,
                strip_decor(row.get("日文店名", "")),
                strip_decor(row.get("店名", "")),
            )
        )

    def find_match(name: str) -> tuple[list[dict], str]:
        """回傳 (候選 csv rows, match 類型)。"""
        norm = normalize(name)
        core = strip_decor(name)

        if norm in idx_jp:
            return idx_jp[norm], "exact-jp"
        if norm in idx_zh:
            return idx_zh[norm], "exact-zh"
        if core in idx_jp_decor:
            return idx_jp_decor[core], "core-jp"
        if core in idx_zh_decor:
            return idx_zh_decor[core], "core-zh"

        # substring: 百名店核心名 ⊆ csv 核心名 (≥4 字)
        if len(core) >= 4:
            hits = [
                row
                for row, jp, zh in decor_pairs
                if (jp and core in jp) or (zh and core in zh)
            ]
            if hits:
                # 命中多筆 → 挑名稱最接近的（最短的 csv 名 = 額外文字最少）
                hits.sort(
                    key=lambda r: min(
                        len(strip_decor(r.get("日文店名", "")) or "x" * 99),
                        len(strip_decor(r.get("店名", "")) or "x" * 99),
                    )
                )
                return hits[: 1 if len(hits) == 1 else 1], (
                    "sub-target-in-csv" if len(hits) == 1 else "sub-target-in-csv-multi"
                )

        # 反向 substring: csv 核心名 ⊆ 百名店核心名 (csv 名 ≥4 字)
        hits = []
        for row, jp, zh in decor_pairs:
            if jp and len(jp) >= 4 and jp in core:
                hits.append(row)
            elif zh and len(zh) >= 4 and zh in core:
                hits.append(row)
        if hits:
            hits.sort(
                key=lambda r: -max(
                    len(strip_decor(r.get("日文店名", ""))),
                    len(strip_decor(r.get("店名", ""))),
                )
            )
            return hits[:1], (
                "sub-csv-in-target" if len(hits) == 1 else "sub-csv-in-target-multi"
            )

        return [], "none"

    matched: dict[str, dict] = {}  # cid → {name, jp_name, awards: []}
    unmatched: list[dict] = []
    match_stats: dict[str, int] = defaultdict(int)

    for rid, entry in raw.items():
        name = entry["name"]
        candidates, mtype = find_match(name)
        match_stats[mtype] += 1

        if candidates:
            row = candidates[0]
            # key 用 lat,lng（5 位小數）對應 map.html 的查表方式
            try:
                key = f"{float(row['緯度']):.5f},{float(row['經度']):.5f}"
            except (KeyError, ValueError, TypeError):
                key = f"cid:{cid_of(row)}" or f"name:{row.get('日文店名', '')}"
            slot = matched.setdefault(
                key,
                {
                    "name": row.get("店名", ""),
                    "jp_name": row.get("日文店名", ""),
                    "tabelog_rid": rid,
                    "tabelog_url": entry["url"],
                    "awards": [],
                },
            )
            for a in entry["awards"]:
                slot["awards"].append(a)
        else:
            unmatched.append(
                {
                    "rid": rid,
                    "name": name,
                    "url": entry["url"],
                    "awards": entry["awards"],
                }
            )

    # 同一店家可能被多筆百名店對到（不太會），這裡只保留 awards 後排序
    for slot in matched.values():
        # dedupe awards
        seen = set()
        out = []
        for a in slot["awards"]:
            k = (a["slug"], a["year"])
            if k not in seen:
                seen.add(k)
                out.append(a)
        out.sort(key=lambda a: (a["label"], a["year"]))
        slot["awards"] = out

    # 寫檔
    Path("cleaned/hyakumeiten_matched.json").write_text(
        json.dumps(matched, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = []
    for u in unmatched:
        labels = ", ".join(f"{a['label']}{a['year']}" for a in u["awards"])
        lines.append(f"{u['name']}  |  {labels}  |  {u['url']}")
    Path("cleaned/hyakumeiten_unmatched.txt").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    # 輸出 hyakumeiten.js 給 map.html 載入
    # 結構: window.__HYAKUMEITEN_DATA = {"lat,lng": [{label, slug, year}, ...]}
    js_payload: dict[str, list[dict]] = {}
    for key, slot in matched.items():
        js_payload[key] = [
            {"label": a["label"], "slug": a["slug"], "year": a["year"]}
            for a in slot["awards"]
        ]
    js_str = json.dumps(js_payload, ensure_ascii=False, indent=2)
    Path("hyakumeiten.js").write_text(
        f"window.__HYAKUMEITEN_DATA = {js_str};\n",
        encoding="utf-8",
    )
    print(f"  已寫 hyakumeiten.js（{len(js_payload)} 家店的徽章資料）")

    print(f"百名店共 {len(raw)} 家京都店家")
    print(f"  對上 csv: {len(matched)} 家")
    print(f"  未對上: {len(unmatched)} 家 → cleaned/hyakumeiten_unmatched.txt")
    print("\nMatch 類型分布:")
    for t, n in sorted(match_stats.items(), key=lambda x: -x[1]):
        print(f"  {t}: {n}")


if __name__ == "__main__":
    main()
