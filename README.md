# Manga Dialogue Replacer 換對白工具

**English summary:** A small Windows desktop tool (Python + tkinter) for manga creators.
Mark dialogue bubbles on a page with red boxes and green X marks, and the tool reads the text
with the Claude Vision API (bring your own API key), optionally translates it from Traditional
Chinese to English or Vietnamese, and generates a Photoshop `.jsx` script that creates text layers at the
marked positions. The interface is in Traditional Chinese.

---

漫畫創作者用的換對白小工具。在稿件上用紅框標出對白、綠 X 標出新文字位置，
工具會用 Claude Vision API 辨識框內文字（可再翻成英文），並產生 Photoshop 的 `.jsx` 腳本，
自動在標記位置建立文字圖層。介面為繁體中文。

> 這是我自己工作流程用的工具，公開出來給有類似需求的人使用或參考。
> 也歡迎拿去修改成適合自己的版本。

## 功能

- **中文換字（直排）**：辨識紅框內的中文，產生直排文字圖層。
- **海外版（中文 → 英文／越南文，橫排）**：整頁辨識＋翻譯，輸出可審稿的對白稿，再產生橫排文字圖層（可選全大寫、白邊）。在面板「翻譯成」選英文或越南文。
- **手寫狀聲字**：藍框標出的狀聲字，海外版會翻成英文或越南文的擬聲詞，用大喊字體小字註記。
- **自動字級**：依框的大小自動決定字級（可設定最小／最大值）。
- 支援單張與批量（資料夾）。
- **稿件格式**：JPG / JPEG / PNG / TIF / TIFF / BMP。透明底的 PNG 會自動墊白底；多頁 TIFF 只讀第一頁。

## 標記規則

在稿件上用**純色**標記（不要透明或羽化）：

| 標記 | 顏色 | 用途 |
|---|---|---|
| 方框 | 紅色 `#FF0000` | 框住要換掉的對白 |
| X | 綠色 `#00FF00` | 新文字要放的中心位置 |
| 方框 | 藍色 `#0000FF` | 手寫狀聲字（海外版用） |

稿件尺寸接近 B4 或 B5 時，程式會自動統一放大到 B4 尺寸再偵測。
存成 JPG 時請用高品質（建議 90 以上），標記顏色才不會因壓縮而變髒。

## 安裝

1. 安裝 [Python](https://www.python.org/downloads/)（開發與測試環境為 Windows + Python 3.10；tkinter 官方安裝程式預設就有）。
2. 在此資料夾開啟終端機，執行：

   ```
   pip install -r requirements.txt
   ```

3. 啟動：

   ```
   python stage1_redbox_gui.py
   ```

4. 在面板的「API 金鑰」欄位輸入你自己的 Anthropic API 金鑰。

## 使用流程

### 流程 A：中文換字（直排）

- **「▶ 執行(直接上字)」**：辨識紅框內文字 → 直接產生 `*_text.jsx`。
- 想先校對錯字：先在海外版區按「① 輸出對白稿」產生 `*_dialogue.txt`，校正「中文：」行的錯字（`／` 代表直排斷欄，請盡量保留），存檔後按「② 中文上字(讀對白稿)」。這一步**不呼叫 API，免費**。

### 流程 B：海外版（中文 → 英文／越南文）

0. 先在面板「翻譯成」選 **英文** 或 **越南文**（切換時會連動預設字體、全大寫和按鈕文字）。
1. 按「① 輸出對白稿」：辨識整頁中文並整頁翻譯，輸出對白稿 txt。已有該語言對白稿的頁面會自動跳過（避免蓋掉審過的稿；要重跑請先刪掉該頁的 txt）。
2. 用文字編輯器打開對白稿，修改「英文：」或「越南文：」那一行。想自己控制斷行，可用 ` / ` 分隔；留空白代表該框不上字。**不要動 `=== 框編號 ===` 那行。**
3. 存檔後按「② ○○上字(讀對白稿)」，產生對應的 JSX。

兩種語言的檔案是分開的，同一頁可以各做一份，不會互相覆蓋：

| 語言 | 對白稿 | JSX |
|---|---|---|
| 英文 | `頁面名_dialogue.txt` | `頁面名_text_EN.jsx` |
| 越南文 | `頁面名_dialogue_VI.txt` | `頁面名_text_VI.jsx` |

**越南文注意事項**
- 字體要有越南文聲調符號。已確認 Patrick Hand（`PatrickHand-Regular`）和 Bangers（`Bangers-Regular`，狀聲字用）有支援越南文；Comic Neue 的越南文支援我沒有確認，請先在 Photoshop 打一段越南文試試。字體檔請依各自的授權使用。
- 切換到越南文時，「全大寫」會預設關閉（聲調符號在大寫較擠，可自行勾選）；行距也會自動加大一點，避免聲調符號互相碰到。
- 翻譯是 AI 的草稿。越南文的人稱代詞（tôi、tớ、cậu、anh、em…）會隨角色關係與語氣改變，**正式發行前建議請越南文母語者校稿**。

### 在 Photoshop 執行 JSX

**檔案 → 指令碼 → 瀏覽 → 選 `.jsx`**，就會依偵測到的位置建立文字圖層。

## 使用前請注意

- **API 金鑰請自備並自行負擔費用。** 金鑰只在執行時輸入，程式不會存檔。請勿把真金鑰寫進程式碼後上傳。
- **稿件框內的局部圖片會被傳送到 Anthropic API 進行辨識與翻譯。** 若稿件有保密需求，請先確認你可以接受。
- **請確認你有權處理、修改或翻譯該稿件。** 使用者需自行負責內容的著作權與授權。
- 辨識提示詞是針對繁體中文原稿寫的；若原稿是其他語言，或要翻成其他語言，請修改 `call_api_for_text()` 和 `translate_dialogues()` 裡的提示詞。
- 程式碼中指定的模型名稱（上述兩個函式內）若日後失效，請換成當時可用的模型。
- 英文字體預設是 `ComicNeue-Bold`，狀聲字註記預設用 `Bangers-Regular`，需自行安裝；沒安裝時 Photoshop 會用預設字體頂替，不會讓腳本失敗。字體檔請依各自的授權使用。
- 中文版產生 JSX 時，為避免文字造成腳本錯誤，會做簡單的字元清理：移除引號、反斜線、部分符號，以及 30 字內的括號註解。需要保留的人，請修改 `generate_jsx()` 裡的 `clean_for_jsx()`。
- 呼叫 API 的按鈕有防連按保護，執行中再按會被擋下，避免重複扣費。
- 這是輔助工具：辨識與翻譯結果請自行檢查，並在 Photoshop 裡個別調整字級與排版。

## 想改成自己的版本？

整個工具是單一檔案、有中文註解。可以把檔案交給 AI 助手（例如 Claude），
說明你要的改動（例如：辨識／翻譯哪種語言、字體、輸出到哪個軟體、稿件尺寸），請它協助修改。

## 授權與聲明

[MIT License](LICENSE) © 2026 YUCHIN LIN
可自由使用、修改、散布與商用，請保留署名與授權聲明。
本軟體以「現狀」提供，不附任何保證；使用風險請自行承擔。

本專案為獨立的個人作品，與 Anthropic 無隸屬或背書關係。Claude 為 Anthropic 的商標。
