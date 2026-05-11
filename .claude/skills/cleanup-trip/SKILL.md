---
name: cleanup-trip
description: Trip 京都美食地圖專案整理 SOP — 清死碼、合併重複結構、刪暫存檔、確認 CLAUDE.md 反映現狀，最後跑 selenium 驗證。
---

# cleanup-trip

對 `D:\Program\ClaudeHTML\Trip` 做一輪健康檢查與整理。**只動 source code 跟暫存檔，不碰資料檔**。

## 不要動

- `all_restaurants.csv`、`data.js`、`prefectures.js` ── 是資料，刪了要重跑 API
- `.env`、`.env.example`、`.gitignore`、`README.md`、`requirements.txt`
- `JF_backup.txt` ── 使用者個人保留檔
- 任何 `transit / 步行時間 / 換乘次數` 相關欄位 ── 是早期 Routes 結果，仍可被新跑沿用

## 步驟

### 1. 盤點
```
ls -la
grep -nE "var\(--[a-z-]+\)" map.html  # 看 CSS var 引用
```
找出：
- 沒人引用的 CSS variable（`grep` 之後比對 `--xxx:` declaration vs `var(--xxx)` 引用）
- `__pycache__/`、`*.log`、`_verify*`、`_screenshot*`、`_overpass*` 等暫存

### 2. 死碼 / 重複
- map.html 的 toggleStatus / assignDay / removeFromDay 三個都會做相同 6-7 行的 sync UI 動作 → 抽 `syncStoreUI(s, { rebuildPlanner })` helper
- 變更欄位 / 訊息：collect.py 有些訊息是「省 $X.XX」依舊照 Routes 單價算 ($0.01) 但現在沒打 Routes，要改成事實描述
- 過時註解：例如 cache 寫「可重用 Routes 資料」但現在實際是「沿用既有 transit 欄位」

### 3. 清檔
```
rm -f collect.log _verify*.py _verify*.png _screenshot*.png _overpass*.json _dom*.html
rm -rf __pycache__
```

### 4. 更新 CLAUDE.md
反映最新狀態：
- 類別總數（grep `CATEGORIES` 對 collect.py 數）
- 總筆數（`wc -l all_restaurants.csv` - 1）
- 新增的檔案 / overlay layer
- 任何新加入的「經驗教訓」段落（CORS、406、headless 注意事項）

### 5. selenium verify
跑一次自動驗證確認沒爛：
- 點任意 marker → `#selected-info` 有內容、`state.selectedId` 非 null
- 勾 `#f-pref` checkbox → DOM path 數 +2（兩府 polygon）
- 取消勾 → -2 還原
- 點「想去」按鈕 → marker style 有黃環

驗證 script 範本（用系統 Chrome、headless=new、隔離 user-data-dir）：

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import os, time
opts = Options()
opts.binary_location = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
opts.add_argument("--headless=new")
opts.add_argument("--user-data-dir=" + os.path.join(os.environ.get("TEMP"), "_chrome_verify"))
driver = webdriver.Chrome(options=opts)
driver.get("file:///D:/Program/ClaudeHTML/Trip/map.html")
driver.execute_script("localStorage.clear();")
driver.refresh()
time.sleep(3)
# ...assertions...
driver.quit()
```

清掉 _verify*.py 跟截圖（暫存）。

### 6. 回報
列出：
- 刪了什麼檔
- 移除了什麼死碼 / 變數
- 抽了什麼 helper
- CLAUDE.md 改了什麼
- verify 結果

## 不要做

- 不要重跑 `collect.py`（會花 API 錢）
- 不要重跑 `generate_prefectures.py`（資料不會變）
- 不要動使用者的 localStorage（除了 verify script 自身）
- 不要刪 `JF_backup.txt`
