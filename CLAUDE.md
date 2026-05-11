# 京都美食地圖（Trip 專案）

## 是什麼
從 Google Places API (New) 抓京都/大阪府餐廳，再用 cid 去重 + 三層過濾分離地標 → 整合成
self-contained HTML 地圖。依區域 / 類別 / 評分 / 中心點篩選，附行程拖拉規劃面板與已訂飯店
釘住。雙擊 `map.html` 即用。

## 檔案
- `collect.py` — Python 收集腳本（Google Places Text Search），跑完自動呼叫 `clean_csv.main()`
- `clean_csv.py` — 清理：cid 去重、三層地標過濾、嚴格類別排除、合併 + 翻譯店名
- `collect_shopping.py` — 購物收集腳本（方式 A 精選名單 + 方式 B 關鍵字補滿到 30）
- `collect_kix.py` — KIX 機場周邊收集腳本（最後一天用：拉麵/甜點/6 子類別購物，純 area_bias 不用精選）
- `refresh_shopping.py` — 購物修正流程（reassign 子類別、refresh 個別精選、跨檔比對標兼類別、dedup）
- `shopping_report.py` — 從 shopping.csv 生 `shopping_summary.md` 報告
- `all_restaurants.csv` — 清理後的餐廳資料（4387 列、13 類）
- `cleaned/attractions.csv` — 從餐廳分離出來的景點（41 列、神社/寺廟/觀光景點）
- `cleaned/shopping.csv` — 購物店家（131 列、6 子類別：職人專門/選物生活/文具/百貨/超市/扭蛋）
   - 多兩欄：`精選`（TRUE / 空）、`兼類別`（同 cid 在 all_restaurants.csv 的餐廳分類，; 分隔）
- `cleaned/kix.csv` — KIX 機場周邊店（110 列、8 類別：拉麵/甜點 + 購物 6 類；欄位與 shopping.csv 對齊）
- `cleaned/shopping_notes.txt` — 需人工確認的購物店註記（refresh_shopping.py 產生）
- `cleaned/dropped_*.csv` — 被砍的飯店 / 商場 / 車站（供 review）
- `osm_pois.json` — Overpass POI cache（一次性，給 clean_csv 30m 比對用；7203 筆）
- `data.js` — `window.__CSV_DATA` + `__ATTRACTIONS_DATA` + `__SHOPPING_DATA` + `__KIX_DATA`，供 `file://` 雙擊讀
- `generate_prefectures.py` — 一次性抓 OSM admin polygon 產 `prefectures.js`
- `prefectures.js` — 京都府 + 大阪府邊界 polygon
- `map.html` — 互動地圖（Leaflet + OpenStreetMap，無後端）
- `.env` — `GOOGLE_MAPS_API_KEY=...`（已 `.gitignore`）

## 執行
```
python collect.py --yes                  # 重抓全 13 類，跑完自動 clean_csv
python collect.py --yes --only=拉麵,丼飯 # 增量補抓
python clean_csv.py --apply              # 只清理（不重抓 API）
python clean_csv.py --dry-run            # 看清理會砍 / 改什麼，不寫檔
python collect_shopping.py --dry-run     # 印購物 API 預估，不打 API
python collect_shopping.py --yes         # 重抓購物 5 子類別（~$2，自動寫 shopping.csv 與 data.js）
python collect_shopping.py --rebuild-from-csv  # 從現有 shopping.csv 跨類別 dedup（不打 API）
python collect_kix.py --dry-run                # 印 KIX 周邊 API 預估，不打 API
python collect_kix.py --yes                    # 重抓 KIX 周邊 8 類別（~$3，自動寫 cleaned/kix.csv 與 data.js）
python collect_kix.py --rebuild-from-csv       # 從現有 cleaned/kix.csv 跨類別 dedup（不打 API）
python refresh_shopping.py --yes               # 跑 reassign + 重抓特定精選 + 標精選/兼類別（會打 ~9 query）
python refresh_shopping.py --yes --skip-api    # 同上但跳過 API 重抓（補精選/兼類別/dedup 用）
python shopping_report.py                      # 從 shopping.csv 生 shopping_summary.md（給 Claude chat 看）
python generate_prefectures.py           # 重抓府界（5-10 年才需要）
```
跑完雙擊 `map.html`。

## 重要技術決策（讀程式前先看這裡）

### Routes API 不再使用
之前用 Routes API TRANSIT 算 60 分鐘範圍。現已改用 Haversine 直線距離取代，
**collect.py 不再呼叫 Routes**。CSV 中既有 transit 欄位的列保留，新撈的店一律 transit 為空。
店家間距離一律用直線估算。如需精確交通時間，等使用者明確選定少量店家才呼叫（嚴格指示）。

### locationBias 必須按區域設定
Text Search 的 `locationBias` 是**強排序提示**，不是硬限制。早期所有區域關鍵字
都用京都駅當 bias 中心，導致偏遠區域（一乗寺、嵐山、下鴨、宇治…）被推到前 60 之外。
修正：`AREA_COORDS` 70 個地點各自的 (lat, lng) + 1.2–2.5km radius，覆蓋市內景點 / 主要 JR
私鐵站 / 京都府市町中心。

### 必加地址過濾
`locationBias` 是 soft hint，會混進 **静岡県清水区**（誤當「清水寺」）、
**埼玉県嵐山町**（誤當「嵐山」）、**東京都**（含「桂」會撈到）等同名地點。
Text Search 結果務必 `"京都府" in formattedAddress`。
**必須完整檢查「京都府」，不能只查「京都」**—— 因為「東京都」這個字串本身含「京都」！

### 品質過濾門檻
- `MIN_RATING = 3.0` — Google 評分 3 顆星以下不收
- `MIN_REVIEWS = 99` — 評論數 < 99 不收
- `HARD_CAP = 1500` — 單類別上限。**先過濾、後截斷**（先截再過濾會誤殺底部好店）

### `regionOf()` 要 cover 反向格式 + 京都府市町
Google 偶爾回反向格式地址（`町名 中京区 京都市 京都府`），不能用 regex `京都市(\S+?区)` 抓。
改成掃 `WARD_TO_REGION` 11 個 ward 名子字串。京都府市町（宇治市 / 亀岡 / 八幡 / 城陽 / 京田辺 等）
也要 if/else 各自歸區，否則全進「其他」分頁。

### file:// CORS：Overpass async fetch 會被擋
雙擊開 `map.html` 是 `file://` 協議，cross-origin fetch (Overpass API) 會被 CORS 擋。
所以府界資料**預先**用 `generate_prefectures.py` 抓成 `prefectures.js`，map.html 用
`<script src>` 載入。OSM POI runtime overlay 已移除，景點全部走 `attractions.csv`（41 家
精選熱門景點 + 評分 + Google Maps URL），完全 self-contained。

### 編碼
Windows 中文系統 cp950 吃不下 emoji，`collect.py` / `clean_csv.py` 開頭強制
`sys.stdout.reconfigure(encoding="utf-8")`。
PowerShell 跑 `python -c "..."` 內含中文要 `$env:PYTHONIOENCODING="utf-8"`。

## clean_csv.py 規則（重點）

### 直接整列丟（不放景點）
1. **車站**：名稱結尾「駅 / 駅前 / station」，且不含餐廳關鍵字
2. **商場**：名稱**開頭**是 イオンモール / BiVi / 京都髙島屋 / 京都ポルタ / タワーテラス 等
   （用 startswith，避免「サイゼリヤ イオンモール」這種真餐廳被誤殺）
3. **純飯店**：名稱命中飯店 regex（`ホテル/HOTEL/Hotel/旅館/\bAPA\b/...` 整詞匹配，
   避免 JAPANESE 中的 `APA` 誤命中）+ priceLevel 空 + 不含餐廳關鍵字

### 移到 attractions.csv（神社 / 寺廟 / 觀光景點）
1. **強地標 suffix**：`寺/神社/神宮/大社/天満宮/八幡宮/宮殿/城` 結尾 + 不含餐廳關鍵字 → 直接判
2. **弱地標 suffix**：`院/堂/閣/苑/殿/塔/廟/門/橋/跡/園` 結尾 + OSM 100m 內有寺廟/神社 +
   評論 > 200 + priceLevel 空
3. 評論 > 10000 + priceLevel 空 + 不含餐廳關鍵字
4. 政府 domain（`.lg.jp / .go.jp`）
5. 評論 = 0 + 不含餐廳關鍵字

### 嚴格類別（避免「鰻魚飯」變垃圾收容所）
collect.py 對「鰻魚飯」用「うなぎ 京都」+ 70 個區域關鍵字廣搜，回來的店多半跟鰻無關，
冷門度排序時會把這些噪音都判給鰻魚飯。修法：以下五個類別**名稱沒命中對應關鍵字就不能贏**：
- 鰻魚飯 ← うなぎ / 鰻 / ひつまぶし
- 章魚燒 ← たこ / 蛸
- 飯糰專賣 ← おにぎり / おむすび
- 炸豬排 ← とんかつ / カツ / 勝牛
- 川床料理 ← 川床 / 納涼床

### 多類別歸屬挑選順序
1. 名稱命中 NAME_RULES 關鍵字（拉麵 / 抹茶 / 居酒屋 / 燒肉 …） → 該類別贏
2. 嚴格類別過濾後，剩下候選按 cat_unique_cid 升序（最少 cid 的類別贏）
3. 同分用 CATEGORIES_ORDER 當 tiebreak

### 合併同 cid 多列
- `日文店名` 取最長
- `店名` 用 `translate_name()` 把日文翻成中文（漢字保留、ラーメン→拉麵 等）
- 其他欄位取最長非空
- 評論數 = 0 → 應該是景點，會走 attraction 規則

### apply 冪等性
重跑 `clean_csv.py --apply` 時會把 `cleaned/attractions.csv` 內容也合回主流程重新分類，
避免第二次 apply 把景點清單清空。

## 資料欄位（CSV）
```
類別, 店名, 日文店名, 地址, 緯度, 經度, 直線距離（km）,
評分, 評論數, 價位等級, 營業時間,
交通時間（分鐘）, 步行時間（分鐘）, 換乘次數,    ← 早期 Routes 結果，新撈的列為空
Google Maps連結, 店家網站, 照片資源
```

## map.html 架構

### marker 樣式
- **餐廳 / 景點**：`L.marker` + `divIcon`（emoji on white circle）。`markerIconFor()` 動態
  生成 HTML，依評分大小（19 / 24 / 30px）+ 狀態著色外環（想去金、排入日色、排除灰、預設淺灰）
- **transit**（✈ 機場 / 🚉 車站 / 🚉 京都駅 / 🏨 飯店）— hardcode list，永遠顯示
- **prefectureLayer**（🗾 京都府 / 大阪府）— 預設關，prefectures.js 載入

### 點 marker 流程
- **不再彈出 leaflet popup**，直接 `m.on("click", () => updateSelectedInfo(s))`
- 右側 planner 頂部 `#selected-info` 用 GM 風格樣式顯示完整資料
- 行程加入選單只在 `status === 'want'` 或已排入時才渲染（候選按鈕代替「移除」）

### 地圖右上 overlay
三個下拉選單（同個 `.map-filter` 框）：
- **📍 中心**：依路線分組的車站 + 飯店清單（出町柳 / 烏丸 / KIX… + 三間飯店）
- **顯示**：全部 / ⭐ 候選 / 📅 8/7…8/15
- **狀態**：全部 / 想去 / 已排入 / 未標記

### 跳下一家
toggleStatus / assignDay / markCandidate 完成後，若已選中心點，會 `jumpToNearestStore`
跳到目前可見 marker 中離中心最近的下一家（自動 panTo + updateSelectedInfo）。

### 飯店 pin
`HOTELS = [...]` 寫死三間已訂飯店（8INN / LEGASTA / KIX 機場套房）。
- 地圖：紫色 🏨 marker，依 displayMode 自動顯/隱（候選 / 全部 → 全顯；指定日期 →
  只顯該日該住、含換房日兩間都顯示）
- 行程：`getHotelsForDay(day)` 把該日該住飯店 pin 在 day-list 最上面，標「入住 / 退房」

### 搜尋框
地圖頂部中央 `.map-search` input。即時搜尋目前可見 marker（過濾後）的店名 / 日文店名 / 地址，
排序按評分高到低，最多 30 筆。點結果 → setView + updateSelectedInfo。

### localStorage（持久化）
`{status, dayPlan, activeDay, activeRegion, displayMode, selectedStation, categories}`

### 字體分級
- 24px（大）：header h1、selected-info 店名、gm-row icon、gm-action 圓 icon
- 20px（中）：h2 段落小標、評分行、gm-row 內文
- 16px（小）：其他全部

## 常加類別怎麼做
1. `collect.py` 加進 `CATEGORIES`（含 `area_term` 用來組區域 keywords）
2. `map.html` 的 `CAT_COLORS` + `CAT_ICONS` 加對應顏色與 emoji
3. `clean_csv.py` 的 `CATEGORIES_ORDER` 加對應順序
4. 若是「廣搜詞」（會抓回大量無關結果），加進 `STRICT_CATS` + 對應名稱關鍵字
5. `python collect.py --yes --only=新類別` 增量抓（會自動跑 clean_csv）

目前 13 餐廳類別：鰻魚飯、丼飯、拉麵、抹茶甜點、燒烤、居酒屋、甜點、章魚燒、炸豬排、
飯糰專賣、日式洋食、日式早餐、川床料理（夏季納涼床）。
3 景點類別（從 attractions.csv 載入）：神社、寺廟、觀光景點。
6 購物類別（從 shopping.csv 載入）：職人專門、選物生活、文具、百貨、超市、扭蛋。
KIX 周邊（從 kix.csv 載入，region = "大阪 KIX"）：8 類別共用前面定義的 emoji / 顏色。

## 購物：兩種收集方式（collect_shopping.py）
- **方式 A**（精選名單）：每個指定店名打 `query = "{店名} 京都"` 的 Text Search，
  取第一筆地址含「京都府」者。`京都站` 自動 normalize 成 `京都駅` 提升命中率。
  精選名單寫死在 `SHOPPING_CATS[<子類別>]["curated"]`。
- **方式 B**（關鍵字補滿）：用日文關鍵字翻 3 頁（最多 60 筆）→ 過濾「京都府」+ 評分 ≥ 3.5
  + 評論 ≥ 30 + dedup 已被精選收走的 place_id → 評論數 desc 排序，補到該子類別 30 間。
- **跨類別 dedup**：寫檔前用 `cid` 全域去重，子類別在字典中的順序即優先序
  （職人專門 > 選物生活 > 文具 > 百貨 > 超市）。同 cid 同時被多類別收走時，前面的贏。
- map.html 直接 push `__SHOPPING_DATA` 到 `state.rows`，5 個子類別已加入 `CAT_COLORS` /
  `CAT_ICONS`，不需要額外 UI 改動。`regionOf()` 對購物地址照常運作（多在中央/東邊）。

## KIX 機場周邊（collect_kix.py + 大阪 KIX 分頁）
最後一天（8/15）住 KIX 機場套房，把行李寄在 JR りんくうタウン駅（改札內 10 處 coin
locker，5:30–23:30），從那站搭 JR 関空快速 / 紀州路快速 ~20 分鐘可達範圍：
関西空港 → りんくうタウン → 日根野 → 熊取 → 和泉砂川 → 東岸和田。

- 不走 collect.py 主流程（避免污染「京都府限定」邏輯）。獨立腳本 `collect_kix.py`：
  - `AREA_COORDS_KIX` 5 站（りんくうタウン / 関西空港 / 泉佐野 / 日根野 / 熊取）
  - 8 個類別：拉麵、甜點 + 6 子類別購物（職人專門/選物生活/文具/百貨/超市/扭蛋）
  - 純 area_bias 不用精選名單（大阪在地店熟悉度低，靠關鍵字 + bias 拉就好）
  - 過濾條件：`"大阪府" in addr` + 含 KIX_AREA_KW 任一市町 + 直線離 りんくう ≤ 13 km
  - 評分 ≥ 3.5、評論 ≥ 30，每類別最多 25 筆，跨類別 cid dedup
- 寫到 `cleaned/kix.csv`，欄位與 `cleaned/shopping.csv` 對齊（含「精選」「兼類別」空欄）。
  data.js 追加 `window.__KIX_DATA`。
- map.html：
  - `REGIONS` 加「大阪 KIX」
  - `csvToObjects(rows, forcedRegion)` 第二參數讓 KIX rows 強制標 region（不走 regionOf）
  - `STATIONS` 加「JR 関空線」optgroup（関西空港/りんくうタウン/日根野/熊取）
  - `DEFAULT_BOUNDS` 南界放寬到 34.36 涵蓋 KIX 區
  - 8 類別已存在 `CAT_COLORS` / `CAT_ICONS`，不必再加

## headless 驗證
改 map.html 後想自動驗：用 `selenium` + 系統 Chrome（不要 Edge）。

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import os
opts = Options()
opts.binary_location = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
opts.add_argument("--headless=new")
opts.add_argument("--user-data-dir=" + os.path.join(os.environ.get("TEMP"), "_chrome_verify"))
driver = webdriver.Chrome(options=opts)
driver.get("file:///D:/Program/ClaudeHTML/Trip/map.html")
# polling DOM / state 驗功能
```

注意：file:// 下 Overpass fetch 會 CORS 失敗（POI 看不到），這是預期行為。
驗證 script 跑完記得清掉 `_verify*.py` / 截圖等暫存。

## 常見故障
- **map.html 雙擊空白**：缺 `data.js` 或 `__CSV_DATA` 為空，重跑 collect.py
- **府界看不到**：缺 `prefectures.js`，跑 `python generate_prefectures.py`
- **景點看不到**：`data.js` 沒含 `__ATTRACTIONS_DATA`，跑 `python clean_csv.py --apply`
- **「大阪 KIX」分頁空的**：`data.js` 沒含 `__KIX_DATA`，跑 `python collect_kix.py --rebuild-from-csv`（不打 API）或 `--yes`（重抓）
- **Routes 403 IP 限制**：GCP Console → Credentials → 把 key 的 Application restrictions 設「無」
- **某區域查不到店**：檢查 `AREA_COORDS` 有沒有該地點 + radius 是否合理
- **Overpass 406 / 没回應**：requests 要設 `User-Agent` header（不設會被拒）
- **APA / ANA 短縮寫誤命中飯店**：HOTEL_PAT 要用 `\bAPA\b` word-boundary，避免在
  「JAPANESE / OHANA」中誤匹配
- **「焼肉特急 亀岡駅」被砍成車站**：`is_station()` 要先 `has_restaurant_kw()` 救回
