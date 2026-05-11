# 京都美食地圖 ・ 2025/8/7-8/15

從京都車站出發 1 小時內可達的鰻魚飯與拉麵店家收集 + 互動地圖。

## 內容物

- `collect.py` — 用 Google Places API (New) + Routes API 抓資料
- `map.html` — 互動地圖檢視（Leaflet + OpenStreetMap）
- `all_restaurants.csv` — 收集後產出
- `data.js` — 供 `map.html` 雙擊開啟用的內嵌資料（同樣由 collect.py 產出）
- `.env.example` — 設定範例

## 1. 環境準備

需要：
- Python 3.10+
- 一個有開通 **Places API (New)** 與 **Routes API** 的 Google Cloud 專案
- API 金鑰

```powershell
# 在專案根目錄
pip install -r requirements.txt
```

## 2. 設定 API 金鑰

```powershell
copy .env.example .env
```

編輯 `.env`：

```
GOOGLE_MAPS_API_KEY=AIzaSy...你的金鑰...
```

> ⚠️ `.env` 已寫進 `.gitignore`，不會被 commit。
> 但若不小心把金鑰公開過，建議到 GCP Console 重發一把。

需在 GCP Console 啟用：
- **Places API (New)**（不是舊版 Places API）
- **Routes API**

## 3. 收集資料

```powershell
python collect.py
```

執行後會：
1. 印出 API 費用上限預估（按 Enter 才會真的呼叫 API）
2. 對每個類別用多組關鍵字 Text Search → 用 `place_id` 去重
3. 對每間店用 Routes API（TRANSIT mode、2025-08-08 12:00 JST 出發）算交通時間
4. 過濾掉 > 60 分鐘的店
5. 輸出 `all_restaurants.csv` 與 `data.js`

費用預估上限約 **\$2.5–3 USD**（每月有 \$200 免費額度）。

## 4. 看地圖

雙擊 `map.html`。需要在同一資料夾有 `data.js`。

備選方案：用本機 HTTP 伺服器
```powershell
python -m http.server 8000
# 開瀏覽器到 http://localhost:8000/map.html
```
這條路會 fetch `all_restaurants.csv`。

### 地圖功能

| 區塊 | 功能 |
|------|------|
| 中央地圖 | 京都車站起點圖釘、30/60 分鐘參考圈、店家 marker（顏色依類別、大小依評分） |
| 左側篩選 | 類別 / 交通時間上限 / 最低評分 / 最少評論數 / 狀態 |
| 點 marker | popup 顯示資訊、3 狀態切換、加入 8/7–8/15 任一天 |
| 右側行程 | 9 天 tab、拖拉重排、店家間步行時間總和（直線估算） |
| localStorage | 自動保存「想去 / 已排入 / 不考慮」與行程安排，重開即恢復 |
| RWD | 寬度 < 900px 改為上下堆疊，篩選與行程面板可收合 |

## 5. 擴充類別

`collect.py` 開頭的 `CATEGORIES` 字典加新類別即可：

```python
CATEGORIES = {
    "鰻魚飯": {...},
    "拉麵": {...},
    "蕎麥麵": {
        "ja_label": "そば",
        "queries": ["そば 京都", "蕎麦 京都"],
    },
}
```

`map.html` 開頭的 `CAT_COLORS` 加對應顏色：

```js
const CAT_COLORS = { "鰻魚飯": "#C0392B", "拉麵": "#FF8C00", "蕎麥麵": "#27AE60" };
```

## 注意事項

- Places API New Text Search 每組關鍵字最多回 60 筆。每類設定 100 間目標時，會用 3 組關鍵字去重後達標。
- 「店家間步行時間總和」是用直線距離 × 1.3 / 4.5 km/h 粗估，不會打 API。
- 若收到很多 `⚠ Routes` 警告，多半是某些店在 12:00 JST 沒有大眾運輸路線可達，會自動跳過。
