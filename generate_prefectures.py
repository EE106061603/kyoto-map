#!/usr/bin/env python3
"""預先抓京都府 + 大阪府 admin polygon，輸出 prefectures.js 給 map.html 直接載入。

避免 file:// 雙擊開 map.html 時的 cross-origin fetch CORS 問題。
跑一次就好（行政區劃幾乎不變），未來邊界更動才需要重跑。

Usage:
    python generate_prefectures.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def assemble_rings(way_list: list[list[int]]) -> list[list[int]]:
    """串接 ways 成 closed rings。每個 way 是一串 node id；方向不固定。"""
    pool = [w[:] for w in way_list]
    rings: list[list[int]] = []
    while pool:
        ring = pool.pop(0)
        changed = True
        while changed and pool:
            changed = False
            for i, w in enumerate(pool):
                if ring[-1] == w[0]:
                    ring = ring + w[1:]; pool.pop(i); changed = True; break
                if ring[-1] == w[-1]:
                    ring = ring + list(reversed(w))[1:]; pool.pop(i); changed = True; break
                if ring[0] == w[-1]:
                    ring = w[:-1] + ring; pool.pop(i); changed = True; break
                if ring[0] == w[0]:
                    ring = list(reversed(w))[:-1] + ring; pool.pop(i); changed = True; break
        if len(ring) > 3 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def main() -> None:
    query = (
        '[out:json][timeout:120];'
        '(relation["ISO3166-2"="JP-26"];relation["ISO3166-2"="JP-27"];);'
        '(._;>;);out;'
    )
    print("抓 Overpass admin_level=4（京都府 + 大阪府）...")
    # 用 POST + 設 User-Agent，Overpass 對沒 UA 的 GET 會回 406
    res = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        headers={"User-Agent": "kyoto-food-map/1.0 (one-time admin polygon fetch)"},
        timeout=180,
    )
    res.raise_for_status()
    data = res.json()

    nodes: dict[int, list[float]] = {}
    ways: dict[int, list[int]] = {}
    relations: list[dict] = []
    for e in data.get("elements", []):
        t = e["type"]
        if t == "node":
            nodes[e["id"]] = [e["lat"], e["lon"]]
        elif t == "way":
            ways[e["id"]] = e["nodes"]
        elif t == "relation":
            relations.append(e)

    print(f"  nodes={len(nodes)} ways={len(ways)} relations={len(relations)}")

    out: list[dict] = []
    for rel in relations:
        name = (rel.get("tags") or {}).get("name", "")
        outer = [
            ways[m["ref"]]
            for m in rel.get("members", [])
            if m["type"] == "way"
            and m.get("role", "") in ("outer", "")
            and m["ref"] in ways
        ]
        rings_ids = assemble_rings(outer)
        rings_ll = [
            [nodes[nid] for nid in r if nid in nodes]
            for r in rings_ids
        ]
        rings_ll = [r for r in rings_ll if len(r) >= 4]
        if not rings_ll:
            continue
        biggest = max(rings_ll, key=len)
        out.append({"name": name, "ring": biggest})
        print(f"  {name}: 主 ring {len(biggest)} 點，丟掉 {len(rings_ll) - 1} 個次要 ring")

    js = "window.__PREFECTURES_DATA = " + json.dumps(out, ensure_ascii=False) + ";\n"
    Path("prefectures.js").write_text(js, encoding="utf-8")
    print(f"\n✅ prefectures.js 已寫出（{len(out)} 府，總 {sum(len(p['ring']) for p in out)} 點）")


if __name__ == "__main__":
    main()
