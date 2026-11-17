# SPDX-License-Identifier: MIT
# Copyright (c) 2026 YUCHIN LIN
"""
換對白工具：GUI 面板版
=====================================
把稿件上標示好的對白框，辨識成文字，再自動在 Photoshop 建立文字圖層。
支援兩條流程：中文換字（直排）、海外版（中文 → 英文／越南文翻譯，橫排）。

支援的稿件格式：JPG / JPEG / PNG / TIF / TIFF / BMP（單張或整個資料夾批量）

標記規則（在稿件上用純色標示）：
  - 紅框 #FF0000：對白框（框住要換掉的對白）
  - 綠 X  #00FF00：新文字要放的中心位置
  - 藍框 #0000FF：手寫狀聲字（海外版會用大喊字體小字註記）

輸出：
  - *_text.jsx      中文版，在 Photoshop 執行，自動建立 T 文字圖層
  - *_text_EN.jsx   海外版英文，同上
  - *_text_VI.jsx   海外版越南文，同上
  - *_dialogue.txt  對白稿（英文；海外版：辨識＋翻譯草稿，給你審稿修改）
  - *_dialogue_VI.txt  對白稿（越南文）

JSX 執行方式：
  Photoshop → 檔案 → 指令碼 → 瀏覽 → 選 .jsx

需要安裝套件：
  pip install pillow opencv-python numpy

注意：辨識與翻譯會把框內局部圖片送到 Anthropic API，需自備 API 金鑰，
費用自付；請確認你有權處理該稿件。金鑰只在執行時輸入，程式不會存檔。
"""

import os
import sys
import re
import base64
import json
import math
import time
import unicodedata
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

# ══════════════════════════════════════════════
#  顏色常數
# ══════════════════════════════════════════════

# 清爽版配色：淺色底＋白卡片＋珊瑚紅點綴
BG_COLOR    = "#b3d9d2"   # 薄荷綠底（深一點，讓白卡片有邊界）
PANEL_COLOR = "#ffffff"   # 白卡片
ACCENT      = "#d9604f"   # 珊瑚紅
BTN_COLOR   = "#c04a3d"
HEADER_BG   = "#9fccc4"   # 標題列比底色再深半階
TEXT_LIGHT  = "#2b3a38"   # 主文字（沿用變數名，值已改深色）
TEXT_DIM    = "#8a9a98"
INPUT_BG    = "#f0f5f4"   # 輸入欄淺灰薄荷（在白卡片上看得出邊界）
INPUT_FG    = "#333333"

# ══════════════════════════════════════════════
#  顏色偵測常數
# ══════════════════════════════════════════════

RED_LOWER1  = np.array([0,   150, 150])
RED_UPPER1  = np.array([10,  255, 255])
RED_LOWER2  = np.array([170, 150, 150])
RED_UPPER2  = np.array([180, 255, 255])
GREEN_LOWER = np.array([50,  150, 150])
GREEN_UPPER = np.array([80,  255, 255])
BLUE_LOWER  = np.array([100, 150, 150])   # 狀聲字框（純藍 #0000FF）
BLUE_UPPER  = np.array([140, 255, 255])
MIN_BOX_AREA = 3000

B4_W, B4_H = 5200, 7340
B5_W = int(B4_W / 1.414)
B5_H = int(B4_H / 1.414)
SIZE_TOLERANCE = 150

# ══════════════════════════════════════════════
#  核心處理函數
# ══════════════════════════════════════════════

def load_page_image(path):
    """讀取稿件圖片（JPG / JPEG / PNG / TIF / TIFF / BMP），統一轉成 8 位元 RGB。
    - 透明底（PNG 等）會墊白底再轉換，避免透明處變成全黑，辨識不到字
    - 調色盤、灰階、CMYK、16 位元灰階也會先轉成一般 RGB
    - 多頁 TIFF 只取第一頁
    """
    img = Image.open(str(path))
    img.seek(0)
    if img.mode in ('I;16', 'I;16L', 'I;16B', 'I'):
        arr = np.array(img).astype(np.float32)
        if img.mode.startswith('I;16') or arr.max() > 255:
            arr = arr / 257.0
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'L')
    if img.mode == 'P' or 'transparency' in img.info:
        img = img.convert('RGBA')
    if img.mode in ('RGBA', 'LA', 'PA'):
        rgba = img.convert('RGBA')
        bg = Image.new('RGB', rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert('RGB')

def detect_and_resize(img_cv):
    h, w = img_cv.shape[:2]
    def near(val, target):
        return abs(val - target) < SIZE_TOLERANCE
    if (near(w, B4_W) and near(h, B4_H)) or (near(w, B4_H) and near(h, B4_W)):
        return img_cv
    if (near(w, B5_W) and near(h, B5_H)) or (near(w, B5_H) and near(h, B5_W)):
        scale = B4_W / w if w < h else B4_H / h
        new_w, new_h = int(w * scale), int(h * scale)
        return cv2.resize(img_cv, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
    return img_cv

def detect_red_boxes(img_cv):
    hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)
    mask1 = cv2.inRange(hsv, RED_LOWER1, RED_UPPER1)
    mask2 = cv2.inRange(hsv, RED_LOWER2, RED_UPPER2)
    red_mask = cv2.bitwise_or(mask1, mask2)
    kernel_close = np.ones((15, 15), np.uint8)
    red_closed = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel_close)
    contours, _ = cv2.findContours(red_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < MIN_BOX_AREA:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        pad = 5
        x1, y1 = max(0, x-pad), max(0, y-pad)
        x2, y2 = min(img_cv.shape[1], x+w+pad), min(img_cv.shape[0], y+h+pad)
        top_edge   = red_mask[y1:y1+10, x1:x2]
        bot_edge   = red_mask[y2-10:y2, x1:x2]
        left_edge  = red_mask[y1:y2, x1:x1+10]
        right_edge = red_mask[y1:y2, x2-10:x2]
        def red_ratio(edge):
            if edge.size == 0: return 0
            return np.sum(edge > 0) / edge.size
        valid_edges = sum([
            red_ratio(top_edge)   > 0.08,
            red_ratio(bot_edge)   > 0.08,
            red_ratio(left_edge)  > 0.08,
            red_ratio(right_edge) > 0.08,
        ])
        if valid_edges < 4:
            continue
        boxes.append({'x': x, 'y': y, 'w': w, 'h': h})
    filtered = []
    for b in boxes:
        dominated = False
        for other in boxes:
            if b == other: continue
            if (other['x'] <= b['x'] and other['y'] <= b['y'] and
                other['x']+other['w'] >= b['x']+b['w'] and
                other['y']+other['h'] >= b['y']+b['h'] and
                other['w']*other['h'] > b['w']*b['h'] * 2):
                dominated = True; break
        if not dominated:
            filtered.append(b)
    filtered.sort(key=lambda b: b['y'])
    return filtered

def detect_blue_boxes(img_cv):
    """狀聲字框（純藍 #0000FF）：輪廓邏輯與紅框同款，只換顏色遮罩"""
    hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)
    blue_mask = cv2.inRange(hsv, BLUE_LOWER, BLUE_UPPER)
    kernel_close = np.ones((15, 15), np.uint8)
    closed = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE, kernel_close)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < MIN_BOX_AREA:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        pad = 5
        x1, y1 = max(0, x-pad), max(0, y-pad)
        x2, y2 = min(img_cv.shape[1], x+w+pad), min(img_cv.shape[0], y+h+pad)
        def blue_ratio(edge):
            if edge.size == 0: return 0
            return np.sum(edge > 0) / edge.size
        valid_edges = sum([
            blue_ratio(blue_mask[y1:y1+10, x1:x2]) > 0.08,
            blue_ratio(blue_mask[y2-10:y2, x1:x2]) > 0.08,
            blue_ratio(blue_mask[y1:y2, x1:x1+10]) > 0.08,
            blue_ratio(blue_mask[y1:y2, x2-10:x2]) > 0.08,
        ])
        if valid_edges < 4:
            continue
        boxes.append({'x': x, 'y': y, 'w': w, 'h': h})
    boxes.sort(key=lambda b: b['y'])
    return boxes

def find_green_center(img_cv, box, pad=20):
    x, y, w, h = box['x'], box['y'], box['w'], box['h']
    x1, y1 = max(0, x-pad), max(0, y-pad)
    x2, y2 = min(img_cv.shape[1], x+w+pad), min(img_cv.shape[0], y+h+pad)
    roi = img_cv[y1:y2, x1:x2]
    hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(hsv_roi, GREEN_LOWER, GREEN_UPPER)
    coords = np.where(green_mask > 0)
    if len(coords[0]) == 0:
        return x + w//2, y + h//2
    return int(np.mean(coords[1])) + x1, int(np.mean(coords[0])) + y1

def call_api_for_text(img_cv, box, api_key, sfx=False):
    import urllib.request, urllib.error
    x, y, w, h = box['x'], box['y'], box['w'], box['h']
    pad = 10
    x1, y1 = max(0, x-pad), max(0, y-pad)
    x2, y2 = min(img_cv.shape[1], x+w+pad), min(img_cv.shape[0], y+h+pad)
    roi = img_cv[y1:y2, x1:x2]
    roi_h, roi_w = roi.shape[:2]
    if roi_w > 800:
        scale = 800 / roi_w
        roi = cv2.resize(roi, (800, int(roi_h * scale)), interpolation=cv2.INTER_AREA)
    _, buf = cv2.imencode('.jpg', roi, [cv2.IMWRITE_JPEG_QUALITY, 90])
    img_b64 = base64.standard_b64encode(buf).decode('utf-8')
    if sfx:
        prompt = """這是漫畫畫面上「手寫狀聲字（擬聲字/音效字）」的局部圖片，請辨識字的內容。
注意：
- 圖上可能有藍色框線和綠色X標記，請忽略這些標記
- 字是徒手寫的，可能變形、有筆勢、融入畫面
- 只回傳辨識出的字本身（例如：砰、轟隆、唰），不要其他說明
- 只回傳「一遍」，不要提供多個版本"""
    else:
        prompt = """這是漫畫對話框的局部圖片，請辨識框內的繁體中文文字內容。
注意：
- 圖上可能有紅色框線和綠色X標記，請忽略這些標記
- 只回傳框內的實際文字內容
- 文字可能是直排或橫排
- 保留標點符號
- 只回傳文字本身，不要其他說明
- 若是直排文字，請依照框內原本的分欄斷行，一欄輸出成一行
- 只回傳「一遍」，不要提供多個版本、不要 --- 分隔線、不要任何排版說明"""
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
            {"type": "text", "text": prompt}
        ]}]
    }).encode('utf-8')
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        return result['content'][0]['text'].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        raise Exception(f"API 錯誤 {e.code}: {body}")

META_LINE = re.compile(r'(排列|排版|辨識|說明|如下|以下)')
PUNCT_NO_HEAD = "，。、！？…：；——"   # 這些不能當欄首

def clean_api_text(raw):
    """洗稿：去掉 AI 夾帶的說明句/分隔線/重複版本；保留斷行（=原稿的斷欄）"""
    lines = []
    for l in raw.strip().splitlines():
        l = l.strip().replace(" ", "").replace("　", "")
        if re.fullmatch(r'[-—–=＝]*', l):
            lines.append("")                       # 分隔線視同空行（塊界）
        elif META_LINE.search(l) and l.endswith(("：", ":")):
            lines.append("")                       # 說明句丟掉
        else:
            lines.append(l)
    blocks, cur = [], []
    for l in lines:
        if l == "":
            if cur: blocks.append(cur); cur = []
        else:
            cur.append(l)
    if cur: blocks.append(cur)
    if not blocks:
        return ""
    if len(blocks) > 1:
        def flat(b): return "".join(b)
        last = set(flat(blocks[-1]))
        dup = all(len(set(flat(b)) & last) / max(1, min(len(set(flat(b))), len(last))) > 0.6
                  for b in blocks[:-1])
        blocks = [blocks[-1]] if dup else [sum(blocks, [])]   # 重複版本→取最後；不同句→全串接
    return "\n".join(blocks[0])

# ══════════════════════════════════════════════
#  海外版（英文）：翻譯＋對白稿＋橫排 JSX
# ══════════════════════════════════════════════

EN_SFX_FONT = "Bangers-Regular"   # 狀聲字註記字體（沒裝時 PS 會用預設字體頂替，不會失敗）

# 海外版目標語言設定（英文檔名維持原樣；越南文另用 _VI，兩種語言可並存、互不覆蓋）
LANGS = {
    'en': {'zh': '英文',   'dlg': '_dialogue.txt',    'jsx': '_text_EN.jsx', 'prefix': 'EN',
           'lead': 1.15, 'font': 'ComicNeue-Bold',      'upper': True},
    'vi': {'zh': '越南文', 'dlg': '_dialogue_VI.txt', 'jsx': '_text_VI.jsx', 'prefix': 'VI',
           'lead': 1.28, 'font': 'PatrickHand-Regular', 'upper': False},
}

def lang_cfg(lang):
    return LANGS.get(lang, LANGS['en'])

def ascii_label(s):
    """圖層名稱用：去掉聲調符號、đ→d，只留英數與空白（PS 圖層名稱較不會出怪事）"""
    s = s.replace('đ', 'd').replace('Đ', 'D')
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^A-Za-z0-9 ]', '', s)

def build_translate_prompt_vi(numbered):
    return f"""你是專業漫畫翻譯。以下是同一頁漫畫各對話框的繁體中文對白（依閱讀順序編號）。
請翻譯成自然、口語的越南文漫畫對白：
- 保持角色語氣與情緒，簡潔有力，像越南母語者會說的話
- 必須使用完整的越南文拼寫與聲調符號（例：à á ả ã ạ、ă â ê ô ơ ư、đ），不可省略聲調
- 依角色之間的關係與語氣選擇合適的人稱代詞（例：tôi、tớ、cậu、anh、em、mày、tao），同一角色前後保持一致
- 使用越南文標點
- 標了(狀聲字)的是畫面上的手寫音效字，請翻成越南漫畫常用的擬聲詞
  （例：砰→BỐP! 轟→ẦM! 唰→VÈO!），全大寫、越短越好，回傳時不要帶(狀聲字)字樣
- 逐框翻譯，每框只輸出一行，格式「編號: 越南文」
- 不要任何說明、不要多個版本

{numbered}"""

def translate_dialogues(zh_items, api_key, lang='en'):
    """整頁對白一次送翻（帶前後文語氣較連貫）。
    zh_items = [(框編號, 中文, kind), ...]（只收非空框；kind='sfx'=狀聲字）→ 回傳 {框編號: 英文}"""
    import urllib.request, urllib.error
    numbered = "\n".join(
        f"{idx}: {'(狀聲字)' if kind == 'sfx' else ''}{t.replace(chr(10), '')}"
        for idx, t, kind in zh_items)
    prompt = build_translate_prompt_vi(numbered) if lang == 'vi' else f"""你是專業漫畫翻譯。以下是同一頁漫畫各對話框的繁體中文對白（依閱讀順序編號）。
請翻譯成自然、口語的英文漫畫對白：
- 保持角色語氣與情緒，簡潔有力，像母語者會說的話
- 使用英文標點
- 標了(狀聲字)的是畫面上的手寫音效字，請翻成歐美漫畫慣用的英文擬聲詞
  （例：砰→BAM! 轟→BOOM! 唰→FWISH!），全大寫、越短越好，回傳時不要帶(狀聲字)字樣
- 逐框翻譯，每框只輸出一行，格式「編號: 英文」
- 不要任何說明、不要多個版本

{numbered}"""
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}]
    }).encode('utf-8')
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        raw = result['content'][0]['text']
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        raise Exception(f"翻譯 API 錯誤 {e.code}: {body}")
    en_map = {}
    for line in raw.splitlines():
        m = re.match(r'\s*(\d+)\s*[:：.、]\s*(.+)', line.strip())
        if m:
            en_map[int(m.group(1))] = m.group(2).strip()
    return en_map

def write_dialogue_file(path, source_name, entries, lang='en'):
    """對白稿：審稿用的 txt。entries = [{'idx','zh','en'}]（含空白框，保住編號對位）
    'en' 欄位存的是「目標語言譯文」（英文或越南文）"""
    zh_name = lang_cfg(lang)['zh']
    example = "Khoan đã! / Tớ đến ngay!" if lang == 'vi' else "Hold on! / I'm coming!"
    lines = [
        f"# 對白稿：{source_name}（共 {len(entries)} 框）",
        "# 中文版：校「中文：」行的錯字（／＝直排斷欄記號，盡量保留），改完按「② 中文上字」。",
        f"# {zh_name}版：改「{zh_name}：」行，改完按「② {zh_name}上字」。＝＝＝框編號那行都不要動＝＝＝",
        f"# {zh_name}想自己控制斷行，可用「 / 」分隔（例：{example}）。",
        f"# (狀聲)框＝畫面手寫音效字，會用大喊字體「小字註記」在綠X位置；辨識不出來就自己填{zh_name}。",
        f"# {zh_name}留空白＝該框略過不上字。改完存檔 → 回面板按「② {zh_name}上字」。",
        "",
    ]
    for e in entries:
        tag = "(狀聲)" if e.get('kind') == 'sfx' else ""
        lines.append(f"=== 框{e['idx']}{tag} ===")
        lines.append("中文：" + (e['zh'].replace("\n", "／") if e['zh'] else f"(辨識不出/空白，{zh_name}請自己填)"))
        lines.append(f"{zh_name}：" + e['en'])
        lines.append("")
    with open(str(path), 'w', encoding='utf-8-sig') as f:
        f.write("\n".join(lines))

def parse_dialogue_file(path):
    """讀審完的對白稿 → {框編號: 英文}；空英文=該框略過"""
    en_map, cur = {}, None
    with open(str(path), 'r', encoding='utf-8-sig') as f:
        content = f.read()
    for raw in content.splitlines():
        line = raw.strip()
        m = re.match(r'===\s*框\s*(\d+)', line)   # 容許「框5(狀聲)」等後綴
        if m:
            cur = int(m.group(1)); continue
        m = re.match(r'(?:英文|越南文|譯文)\s*[:：]\s*(.*)', line)
        if m and cur is not None:
            en_map[cur] = m.group(1).strip()
    return en_map

def parse_dialogue_zh(path):
    """讀對白稿的「中文：」行 → {框編號: 中文}（校完錯字的版本）。
    「／」還原成斷欄；括號佔位字樣（辨識不出/空白框）＝空＝跳過"""
    zh_map, cur = {}, None
    with open(str(path), 'r', encoding='utf-8-sig') as f:
        content = f.read()
    for raw in content.splitlines():
        line = raw.strip()
        m = re.match(r'===\s*框\s*(\d+)', line)
        if m:
            cur = int(m.group(1)); continue
        m = re.match(r'中文\s*[:：]\s*(.*)', line)
        if m and cur is not None:
            t = m.group(1).strip()
            if re.fullmatch(r'[(（].*[)）]', t):
                t = ""                      # 佔位說明字樣不是對白
            zh_map[cur] = t.replace("／", "\n")
    return zh_map

def process_page_chinese(input_path, output_dir, font_name, font_size, chars_per_col,
                         line_spacing, tracking, white_stroke, stroke_size, log,
                         auto_size=True, fs_min=24, fs_max=200):
    """中文流程②：讀校完錯字的 *_dialogue.txt ＋ 重測紅框 → 產 *_text.jsx（直排，走原本排版）。
    只處理紅框（中文版的狀聲字是原稿上的手寫字，不用上字）"""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    dlg_path = output_dir / f"{input_path.stem}_dialogue.txt"
    out_jsx = output_dir / f"{input_path.stem}_text.jsx"

    log(f"② 中文上字：{input_path.name}")
    if not dlg_path.is_file():
        log(f"  [錯誤] 找不到對白稿 {dlg_path.name}，請先跑「① 輸出對白稿」"); return
    try:
        zh_map = parse_dialogue_zh(dlg_path)
    except Exception as e:
        log(f"  [錯誤] 對白稿讀取失敗：{e}"); return

    try:
        pil_img = load_page_image(input_path)
        img_cv = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except Exception as e:
        log(f"  [錯誤] 無法讀取：{e}"); return

    img_cv = detect_and_resize(img_cv)
    img_h, img_w = img_cv.shape[:2]
    boxes = detect_red_boxes(img_cv)
    log(f"  偵測到 {len(boxes)} 個對話框(紅)，對白稿有 {len(zh_map)} 框")

    text_layers = []
    for i, box in enumerate(boxes):
        zh = zh_map.get(i + 1, '').strip()
        if not zh:
            log(f"  [框{i+1}] 中文空白，略過"); continue
        cx, cy = find_green_center(img_cv, box)
        text_layers.append({'text': zh, 'cx': cx, 'cy': cy,
                            'box_w': box['w'], 'box_h': box['h'],
                            'label': re.sub(r'[\n\r:：]', '', zh[:4])})
    if not text_layers:
        log("  [警告] 沒有任何可上字的框"); return

    generate_jsx(text_layers, img_w, img_h, out_jsx, input_path.name,
                 font_name, font_size, chars_per_col, line_spacing,
                 tracking, white_stroke, stroke_size,
                 auto_size, fs_min, fs_max)
    log(f"  ✅ 完成！{len(text_layers)} 個中文圖層 → {out_jsx.name}")

def wrap_english(text, box_w, box_h, widen=1.0):
    """英文斷行：按單字斷、各行長度平衡，行數依框形反推（泡泡越高行數越多）。
    widen=文字塊相對紅框的放寬倍率（瘦高中文泡泡塞橫排英文用）。
    每行超過目標長度就斷、不設行數上限 → 不會有「剩字全塞最後一行」的爆框。"""
    manual = [seg.strip() for seg in text.split("/") if seg.strip()]
    if len(manual) > 1:
        return manual                      # 使用者手動用 / 斷行 → 直接沿用
    words = text.split()
    if not words:
        return []
    total = len(" ".join(words))
    eff_w = max(box_w, 1) * widen
    # 行寬≈字數×0.55字高、行高≈1.15字高 → 使文字塊長寬比貼近（放寬後的）框
    n_lines = max(1, round(math.sqrt(0.45 * total * box_h / eff_w)))
    n_lines = min(n_lines, len(words))
    target = max(total / n_lines, max(len(w) for w in words))
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if cur and len(cand) > target:
            lines.append(cur); cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines

def clean_en_for_jsx(t):
    """英文版轉義：保留撇號（I'm 不能斷手）、彎引號轉直、雙引號跳脫"""
    for src, dst in [
        ("’", "'"), ("‘", "'"),
        ("“", '"'), ("”", '"'),
        ("…", "..."), ("—", "--"), ("–", "-"),
    ]:
        t = t.replace(src, dst)
    t = re.sub(r'[\x00-\x09\x0e-\x1f\x7f]', '', t)
    t = t.replace("\\", "")
    t = t.replace('"', '\\"')
    t = t.replace("\r", "\\r")
    return t

def _jsx_white_stroke_lines():
    """白邊描邊函式（經典 Lefx 寫法）；與中文版同款，抽出來給英文 JSX 用"""
    return [
        '// 白邊描邊（經典 Lefx 寫法；size 單位=像素）',
        'function addWhiteStroke(sizePx) {',
        '    var desc = new ActionDescriptor();',
        '    var ref = new ActionReference();',
        '    ref.putProperty(charIDToTypeID("Prpr"), charIDToTypeID("Lefx"));',
        '    ref.putEnumerated(charIDToTypeID("Lyr "), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));',
        '    desc.putReference(charIDToTypeID("null"), ref);',
        '    var lefx = new ActionDescriptor();',
        '    lefx.putUnitDouble(charIDToTypeID("Scl "), charIDToTypeID("#Prc"), 100);',
        '    var frfx = new ActionDescriptor();',
        '    frfx.putBoolean(charIDToTypeID("enab"), true);',
        '    frfx.putEnumerated(charIDToTypeID("Styl"), charIDToTypeID("FStl"), charIDToTypeID("OutF"));',
        '    frfx.putEnumerated(charIDToTypeID("PntT"), charIDToTypeID("FrFl"), charIDToTypeID("SClr"));',
        '    frfx.putEnumerated(charIDToTypeID("Md  "), charIDToTypeID("BlnM"), charIDToTypeID("Nrml"));',
        '    frfx.putUnitDouble(charIDToTypeID("Opct"), charIDToTypeID("#Prc"), 100);',
        '    frfx.putUnitDouble(charIDToTypeID("Sz  "), charIDToTypeID("#Pxl"), sizePx);',
        '    var clr = new ActionDescriptor();',
        '    clr.putDouble(charIDToTypeID("Rd  "), 255);',
        '    clr.putDouble(charIDToTypeID("Grn "), 255);',
        '    clr.putDouble(charIDToTypeID("Bl  "), 255);',
        '    frfx.putObject(charIDToTypeID("Clr "), charIDToTypeID("RGBC"), clr);',
        '    lefx.putObject(charIDToTypeID("FrFX"), charIDToTypeID("FrFX"), frfx);',
        '    desc.putObject(charIDToTypeID("T   "), charIDToTypeID("Lefx"), lefx);',
        '    executeAction(charIDToTypeID("setd"), desc, DialogModes.NO);',
        '}',
        '',
    ]

def generate_jsx_en(text_layers, img_w, img_h, output_jsx_path, source_filename,
                    font_name, uppercase, white_stroke, stroke_size,
                    fs_min=24, fs_max=200, lang='en'):
    """英文版 JSX：橫排、置中、按單字斷行；定位/px2pt/白邊沿用中文版的血淚成果
    lang='vi' 時行距加大（聲調符號疊在字上方，需要多一點空間）"""
    cfg = lang_cfg(lang)
    zh_name = cfg['zh']
    lead_k = cfg['lead']
    jsx = []
    jsx.append(f'// 自動產生的{zh_name}文字圖層腳本（海外版）')
    jsx.append(f'// 來源：{source_filename}')
    jsx.append(f'// 分析時圖片尺寸：{img_w}x{img_h}（座標依 PS 文件實際尺寸換算，B4/B5 皆準）')
    jsx.append('#target photoshop')
    jsx.append('')
    jsx.append('app.bringToFront();')
    jsx.append('if (app.documents.length == 0) {')
    jsx.append('    alert("請先在 Photoshop 開啟要上字的稿件（海外版底稿），再執行本指令碼");')
    jsx.append('} else {')
    jsx.append('var _ru = app.preferences.rulerUnits;')
    jsx.append('var _tu = app.preferences.typeUnits;')
    jsx.append('app.preferences.rulerUnits = Units.PIXELS;')
    jsx.append('app.preferences.typeUnits = TypeUnits.POINTS;')
    jsx.append('var doc = app.activeDocument;')
    jsx.append(f'var kx = doc.width.as("px") / {img_w};')
    jsx.append(f'var ky = doc.height.as("px") / {img_h};')
    jsx.append('var px2pt = 72 / doc.resolution;  // 600dpi 稿不爆字的保命換算')
    jsx.append('var errs = [];')
    jsx.append('')
    if white_stroke:
        jsx.extend(_jsx_white_stroke_lines())

    for i, layer in enumerate(text_layers):
        text = layer['text']
        cx, cy = layer['cx'], layer['cy']
        box_w, box_h = layer['box_w'], layer['box_h']
        is_sfx = layer.get('sfx', False)
        if uppercase or is_sfx:
            text = text.upper()          # 狀聲字一律大寫（擬聲詞慣例）
        if is_sfx:
            # 狀聲字＝小字註記：不塞滿框，字級跟著原手寫字的大小走（框短邊的28%）
            segs = [s.strip() for s in text.split("/") if s.strip()]
            if len(segs) > 1:
                lines = segs
            else:
                words = text.split()
                if len(text) > 14 and len(words) > 1:
                    mid = (len(words) + 1) // 2
                    lines = [" ".join(words[:mid]), " ".join(words[mid:])]
                else:
                    lines = [text]
            if not lines:
                continue
            n_lines = len(lines)
            font_px = int(max(fs_min, min(fs_max, min(box_w, box_h) * 0.28)))
            lead_px = round(font_px * 1.05)
        else:
            # 瘦高中文泡泡塞橫排英文：試幾檔放寬倍率，挑字級最大的一檔
            # （置中錨定在綠X，放寬=左右對稱外擴；配白邊壓到泡泡邊也清楚）
            best = None
            for widen in (1.0, 1.2, 1.35, 1.5):
                cand_lines = wrap_english(text, box_w, box_h, widen)
                if not cand_lines:
                    break
                cn = len(cand_lines)
                cmax = max(len(l) for l in cand_lines)
                f_by_w = (box_w * widen * 0.80) / (cmax * 0.55)
                f_by_h = (box_h * 0.80) / (cn * lead_k)
                f = min(f_by_w, f_by_h)
                if best is None or f > best[0] + 0.5:   # 要明顯更大才放寬（同分取不超框的）
                    best = (f, cand_lines)
            if best is None:
                continue
            lines = best[1]
            n_lines = len(lines)
            font_px = int(max(fs_min, min(fs_max, best[0])))
            lead_px = round(font_px * lead_k)
        text_esc = clean_en_for_jsx("\r".join(lines))
        label = ascii_label(lines[0])[:12].strip() or f"box{i+1}"
        layer_font = EN_SFX_FONT if is_sfx else font_name
        layer_prefix = "SFX" if is_sfx else cfg['prefix']

        jsx.append(f'// ── 框{i+1}：{label}（框 {box_w}x{box_h}，{n_lines} 行{"，狀聲註記" if is_sfx else ""}）──')
        jsx.append('try {')
        jsx.append('    var tl = doc.artLayers.add();')
        jsx.append('    tl.kind = LayerKind.TEXT;')
        jsx.append(f'    tl.name = "{layer_prefix}{i+1:02d}_{label}";')
        jsx.append('    var ti = tl.textItem;')
        jsx.append(f'    ti.contents = "{text_esc}";')
        jsx.append(f'    ti.font = "{layer_font}";')
        jsx.append(f'    ti.size = new UnitValue(Math.round({font_px} * kx * px2pt * 10) / 10, "pt");')
        jsx.append('    ti.direction = Direction.HORIZONTAL;')
        jsx.append('    ti.kind = TextType.POINTTEXT;')
        jsx.append('    ti.justification = Justification.CENTER;')
        jsx.append('    ti.useAutoLeading = false;')
        jsx.append(f'    ti.leading = new UnitValue(Math.round({lead_px} * kx * px2pt * 10) / 10, "pt");')
        jsx.append('    var col = new SolidColor();')
        jsx.append('    col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;')
        jsx.append('    ti.color = col;')
        jsx.append('    var b = tl.bounds;')
        jsx.append('    var bcx = (b[0].as("px") + b[2].as("px")) / 2;')
        jsx.append('    var bcy = (b[1].as("px") + b[3].as("px")) / 2;')
        jsx.append(f'    tl.translate({cx} * kx - bcx, {cy} * ky - bcy);')
        if white_stroke:
            jsx.append('    doc.activeLayer = tl;')
            jsx.append(f'    addWhiteStroke(Math.max(1, Math.round({stroke_size} * kx)));')
        jsx.append(f'}} catch(e) {{ errs.push("框{i+1}: " + e); }}')
        jsx.append('')

    jsx.append('app.preferences.rulerUnits = _ru;')
    jsx.append('app.preferences.typeUnits = _tu;')
    jsx.append('if (errs.length > 0) { alert("完成，但有 " + errs.length + " 框失敗：\\n" + errs.join("\\n")); }')
    jsx.append(f'else {{ alert("完成！共建立{zh_name}文字圖層，全部成功"); }}')
    jsx.append('}  // else 結束（有開稿件才執行）')

    with open(str(output_jsx_path), 'w', encoding='utf-8') as f:
        f.write('\n'.join(jsx))

def process_page_dialogue_export(input_path, output_dir, api_key, log, lang='en'):
    """海外版流程①：辨識全頁中文＋整頁翻譯（英文或越南文）→ 輸出對白稿 txt 給使用者審稿"""
    cfg = lang_cfg(lang)
    zh_name = cfg['zh']
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_txt = output_dir / f"{input_path.stem}{cfg['dlg']}"

    # 防呆＋斷點續跑：已有對白稿的頁一律跳過（防止手滑蓋掉審過的稿、
    # 也讓大批量斷掉後重按①自動從沒跑過的頁接續）。要重跑該頁＝先刪它的 txt。
    if out_txt.is_file():
        log(f"① {input_path.name} → 已有 {out_txt.name}，跳過（要重跑請先刪掉該 txt）")
        return

    log(f"① 對白稿：{input_path.name}")
    try:
        pil_img = load_page_image(input_path)
        img_cv = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except Exception as e:
        log(f"  [錯誤] 無法讀取：{e}"); return

    img_cv = detect_and_resize(img_cv)
    boxes = detect_red_boxes(img_cv)
    sfx_boxes = detect_blue_boxes(img_cv)
    log(f"  偵測到 {len(boxes)} 個對話框(紅)、{len(sfx_boxes)} 個狀聲字框(藍)")
    if not boxes and not sfx_boxes:
        log("  [警告] 沒有偵測到紅框/藍框"); return

    all_boxes = [(b, 'dlg') for b in boxes] + [(b, 'sfx') for b in sfx_boxes]
    entries = []
    for i, (box, kind) in enumerate(all_boxes):
        try:
            text = clean_api_text(call_api_for_text(img_cv, box, api_key, sfx=(kind == 'sfx')))
        except Exception as e:
            log(f"  [框{i+1}] [API錯誤] {e}"); text = ""
        is_placeholder = any(kw in text for kw in ['框內空白', '無文字', '不清晰', '無可辨識', '圖中'])
        if is_placeholder or box['w'] < 50 or box['h'] < 50:
            text = ""
        tag = "狀聲" if kind == 'sfx' else ""
        log(f"  [框{i+1}{tag}] 「{text.replace(chr(10), '／') if text else '(認不出，txt 可手填' + zh_name + ')'}」")
        entries.append({'idx': i + 1, 'zh': text, 'en': '', 'kind': kind})
        time.sleep(0.3)

    zh_items = [(e['idx'], e['zh'], e['kind']) for e in entries if e['zh']]
    if zh_items:
        log(f"  翻譯中（{len(zh_items)} 框整頁送翻）...")
        try:
            en_map = translate_dialogues(zh_items, api_key, lang)
            for e in entries:
                e['en'] = en_map.get(e['idx'], '')
            missing = [e['idx'] for e in entries if e['zh'] and not e['en']]
            if missing:
                log(f"  [注意] 框 {missing} 沒拿到翻譯，txt 裡是空的，請自行補上")
        except Exception as e:
            log(f"  [翻譯失敗] {e}（txt 照樣輸出，{zh_name}欄空白可手填）")

    write_dialogue_file(out_txt, input_path.name, entries, lang)
    log(f"  ✅ 對白稿 → {out_txt.name}（請開啟審稿，改完按「② {zh_name}上字」）")

def process_page_english(input_path, output_dir, font_name, uppercase,
                         white_stroke, stroke_size, log, fs_min=24, fs_max=200, lang='en'):
    """海外版流程②：讀審完的對白稿 ＋ 重測紅框 → 產 *_text_EN.jsx / *_text_VI.jsx"""
    cfg = lang_cfg(lang)
    zh_name = cfg['zh']
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    dlg_path = output_dir / f"{input_path.stem}{cfg['dlg']}"
    out_jsx = output_dir / f"{input_path.stem}{cfg['jsx']}"

    log(f"② {zh_name}上字：{input_path.name}")
    if not dlg_path.is_file():
        log(f"  [錯誤] 找不到對白稿 {dlg_path.name}，請先跑「① 輸出對白稿」"); return
    try:
        en_map = parse_dialogue_file(dlg_path)
    except Exception as e:
        log(f"  [錯誤] 對白稿讀取失敗：{e}"); return

    try:
        pil_img = load_page_image(input_path)
        img_cv = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except Exception as e:
        log(f"  [錯誤] 無法讀取：{e}"); return

    img_cv = detect_and_resize(img_cv)
    img_h, img_w = img_cv.shape[:2]
    boxes = detect_red_boxes(img_cv)
    sfx_boxes = detect_blue_boxes(img_cv)
    log(f"  偵測到 {len(boxes)} 個對話框(紅)、{len(sfx_boxes)} 個狀聲字框(藍)，對白稿有 {len(en_map)} 框")

    all_boxes = [(b, 'dlg') for b in boxes] + [(b, 'sfx') for b in sfx_boxes]
    text_layers = []
    for i, (box, kind) in enumerate(all_boxes):
        en = en_map.get(i + 1, '').strip()
        if not en:
            log(f"  [框{i+1}] {zh_name}空白，略過"); continue
        # 狀聲框的綠X可以點在框外一點（註記要放旁邊），搜尋範圍放寬
        cx, cy = find_green_center(img_cv, box, pad=(60 if kind == 'sfx' else 20))
        text_layers.append({'text': en, 'cx': cx, 'cy': cy,
                            'box_w': box['w'], 'box_h': box['h'],
                            'sfx': kind == 'sfx'})
    if not text_layers:
        log("  [警告] 沒有任何可上字的框"); return

    generate_jsx_en(text_layers, img_w, img_h, out_jsx, input_path.name,
                    font_name, uppercase, white_stroke, stroke_size, fs_min, fs_max, lang)
    log(f"  ✅ 完成！{len(text_layers)} 個{zh_name}圖層 → {out_jsx.name}")

def generate_jsx(text_layers, img_w, img_h, output_jsx_path, source_filename,
                 font_name, font_size, chars_per_col, line_spacing,
                 tracking, white_stroke, stroke_size,
                 auto_size=True, fs_min=24, fs_max=200):
    jsx = []
    jsx.append('// 自動產生的文字圖層腳本')
    jsx.append(f'// 來源：{source_filename}')
    jsx.append(f'// 分析時圖片尺寸：{img_w}x{img_h}（座標依 PS 文件實際尺寸換算，B4/B5 皆準）')
    jsx.append('#target photoshop')
    jsx.append('')
    jsx.append('app.bringToFront();')
    jsx.append('if (app.documents.length == 0) {')
    jsx.append('    alert("請先在 Photoshop 開啟要上字的稿件（合併稿），再執行本指令碼");')
    jsx.append('} else {')
    jsx.append('// 鎖定單位為像素（跑完還原），座標才不會被 mm/pt 尺規解讀歪掉')
    jsx.append('var _ru = app.preferences.rulerUnits;')
    jsx.append('var _tu = app.preferences.typeUnits;')
    jsx.append('app.preferences.rulerUnits = Units.PIXELS;')
    jsx.append('app.preferences.typeUnits = TypeUnits.POINTS;')
    jsx.append('var doc = app.activeDocument;')
    jsx.append(f'var kx = doc.width.as("px") / {img_w};   // 分析圖 → PS 文件的比例')
    jsx.append(f'var ky = doc.height.as("px") / {img_h};')
    jsx.append('var px2pt = 72 / doc.resolution;  // 像素→點：依文件實際 DPI 換算（600dpi 稿不再爆字）')
    jsx.append('var errs = [];')
    jsx.append('')
    if white_stroke:
        jsx.append('// 白邊描邊（經典 Lefx 寫法；size 單位=像素）')
        jsx.append('function addWhiteStroke(sizePx) {')
        jsx.append('    var desc = new ActionDescriptor();')
        jsx.append('    var ref = new ActionReference();')
        jsx.append('    ref.putProperty(charIDToTypeID("Prpr"), charIDToTypeID("Lefx"));')
        jsx.append('    ref.putEnumerated(charIDToTypeID("Lyr "), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));')
        jsx.append('    desc.putReference(charIDToTypeID("null"), ref);')
        jsx.append('    var lefx = new ActionDescriptor();')
        jsx.append('    lefx.putUnitDouble(charIDToTypeID("Scl "), charIDToTypeID("#Prc"), 100);')
        jsx.append('    var frfx = new ActionDescriptor();')
        jsx.append('    frfx.putBoolean(charIDToTypeID("enab"), true);')
        jsx.append('    frfx.putEnumerated(charIDToTypeID("Styl"), charIDToTypeID("FStl"), charIDToTypeID("OutF"));')
        jsx.append('    frfx.putEnumerated(charIDToTypeID("PntT"), charIDToTypeID("FrFl"), charIDToTypeID("SClr"));')
        jsx.append('    frfx.putEnumerated(charIDToTypeID("Md  "), charIDToTypeID("BlnM"), charIDToTypeID("Nrml"));')
        jsx.append('    frfx.putUnitDouble(charIDToTypeID("Opct"), charIDToTypeID("#Prc"), 100);')
        jsx.append('    frfx.putUnitDouble(charIDToTypeID("Sz  "), charIDToTypeID("#Pxl"), sizePx);')
        jsx.append('    var clr = new ActionDescriptor();')
        jsx.append('    clr.putDouble(charIDToTypeID("Rd  "), 255);')
        jsx.append('    clr.putDouble(charIDToTypeID("Grn "), 255);')
        jsx.append('    clr.putDouble(charIDToTypeID("Bl  "), 255);')
        jsx.append('    frfx.putObject(charIDToTypeID("Clr "), charIDToTypeID("RGBC"), clr);')
        jsx.append('    lefx.putObject(charIDToTypeID("FrFX"), charIDToTypeID("FrFX"), frfx);')
        jsx.append('    desc.putObject(charIDToTypeID("T   "), charIDToTypeID("Lefx"), lefx);')
        jsx.append('    executeAction(charIDToTypeID("setd"), desc, DialogModes.NO);')
        jsx.append('}')
        jsx.append('')

    for i, layer in enumerate(text_layers):
        text  = layer['text']
        cx, cy = layer['cx'], layer['cy']
        box_w, box_h = layer['box_w'], layer['box_h']
        label = layer['label']
        # 斷欄：AI 帶回原稿斷行（多行）→ 直接沿用；否則公式計算＋欄首禁標點
        src_lines = [l for l in text.split("\n") if l]
        flat_text = text.replace("\n", "")
        n_chars = max(1, len(flat_text))
        if auto_size and len(src_lines) > 1:
            cols = src_lines                       # 原稿的斷行就是每欄字數的正解
        else:
            if auto_size:
                col_chars = round(math.sqrt(1.15 * n_chars * box_h / max(box_w, 1)))
                col_chars = max(2, min(20, col_chars, n_chars))
            else:
                col_chars = chars_per_col
            cols, cur = [], ""
            for ci, ch in enumerate(flat_text):
                cur += ch
                nxt = flat_text[ci + 1] if ci + 1 < len(flat_text) else ""
                if len(cur) >= col_chars and nxt and nxt not in PUNCT_NO_HEAD:
                    cols.append(cur); cur = ""
            if cur: cols.append(cur)
        n_cols = len(cols)
        col_max = max(len(c) for c in cols)
        text_with_breaks = "\r".join(cols)

        # 字級：行距（欄與欄）7/6＝18px字→21px；上下字密排佔 1 倍字高；每框各自反推
        PITCH = 7 / 6
        font_px, lead_px = 0, 0
        if auto_size:
            f_by_h = (box_h * 0.78) / col_max           # 密排：欄高=字數×字高
            f_by_w = (box_w * 0.78) / (n_cols * PITCH)  # 欄距照 7/6
            font_px = int(max(fs_min, min(fs_max, min(f_by_h, f_by_w))))
            lead_px = round(font_px * PITCH)

        def clean_for_jsx(t):
            import re as _re
            t = t.replace("\n", "\r")
            t = t.replace("\\", "").replace('"', "").replace("'", "")
            for src, dst in [
                ("\u22ef","..."),("\u2026","..."),
                ("\u2014","--"), ("\u2013","-"),
                ("\u00d7","x"),  ("\u300a",""),("\u300b",""),
                ("\u3010",""),   ("\u3011",""),
                ("\u2605",""),   ("\u25a0",""),
                ("\u25cb","o"),  ("\u25cf","o"),
                ("\uff1a",":"),
            ]:
                t = t.replace(src, dst)
            t = _re.sub(r'（[^）]{0,30}）', '', t)
            t = _re.sub(r'\([^)]{0,30}\)', '', t)
            t = _re.sub(r'[\x00-\x09\x0e-\x1f\x7f]', '', t)
            t = t.strip()
            t = t.replace("\r", "\\r")
            return t
        text_esc = clean_for_jsx(text_with_breaks)

        jsx.append(f'// ── 框{i+1}：{label}（框 {box_w}x{box_h}，{n_chars} 字排 {n_cols} 欄）──')
        jsx.append('try {')
        jsx.append('    var tl = doc.artLayers.add();')
        jsx.append('    tl.kind = LayerKind.TEXT;')
        jsx.append(f'    tl.name = "對白{i+1:02d}_{label}";')
        jsx.append('    var ti = tl.textItem;')
        jsx.append(f'    ti.contents = "{text_esc}";')
        jsx.append(f'    ti.font = "{font_name}";')
        if auto_size:
            jsx.append(f'    ti.size = new UnitValue(Math.round({font_px} * kx * px2pt * 10) / 10, "pt");  // 依框自動字級')
        else:
            jsx.append(f'    ti.size = new UnitValue({font_size}, "pt");')
        jsx.append('    ti.direction = Direction.VERTICAL;')
        jsx.append('    ti.kind = TextType.POINTTEXT;')

        # 行距（直排文字的 leading = 欄距）
        if auto_size:
            jsx.append('    ti.useAutoLeading = false;')
            jsx.append(f'    ti.leading = new UnitValue(Math.round({lead_px} * kx * px2pt * 10) / 10, "pt");')
        elif line_spacing > 0:
            jsx.append(f'    ti.useAutoLeading = false;')
            jsx.append(f'    ti.leading = new UnitValue({line_spacing}, "pt");')

        # 字距（tracking，單位 1/1000 em）；上下字密排（跟原稿一致），要鬆用 GUI 字距欄
        if tracking != 0:
            jsx.append(f'    ti.tracking = {tracking};')

        # 文字顏色（黑色）
        jsx.append('    var col = new SolidColor();')
        jsx.append('    col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;')
        jsx.append('    ti.color = col;')

        # 絕對定位：量文字層實際渲染邊界 → 平移到綠X中心（直排/欄數多少都準）
        jsx.append('    var b = tl.bounds;')
        jsx.append('    var bcx = (b[0].as("px") + b[2].as("px")) / 2;')
        jsx.append('    var bcy = (b[1].as("px") + b[3].as("px")) / 2;')
        jsx.append(f'    tl.translate({cx} * kx - bcx, {cy} * ky - bcy);')

        # 白邊描邊（定位之後才上，避免描邊撐大邊界影響置中；厚度隨文件比例縮放）
        if white_stroke:
            jsx.append('    doc.activeLayer = tl;')
            jsx.append(f'    addWhiteStroke(Math.max(1, Math.round({stroke_size} * kx)));')

        jsx.append(f'}} catch(e) {{ errs.push("框{i+1}: " + e); }}')
        jsx.append('')

    jsx.append('app.preferences.rulerUnits = _ru;')
    jsx.append('app.preferences.typeUnits = _tu;')
    jsx.append(f'if (errs.length > 0) {{ alert("完成 {len(text_layers)} 框，但有 " + errs.length + " 框失敗：\\n" + errs.join("\\n")); }}')
    jsx.append(f'else {{ alert("完成！共建立 {len(text_layers)} 個文字圖層，全部成功"); }}')
    jsx.append('}  // else 結束（有開稿件才執行）')

    with open(str(output_jsx_path), 'w', encoding='utf-8') as f:
        f.write('\n'.join(jsx))

def process_page(input_path, output_dir, api_key,
                 font_name, font_size, chars_per_col, line_spacing,
                 tracking, white_stroke, stroke_size, log,
                 auto_size=True, fs_min=24, fs_max=200):
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = input_path.stem
    output_jsx   = output_dir / f"{stem}_text.jsx"

    log(f"處理：{input_path.name}")
    try:
        pil_img = load_page_image(input_path)
        img_cv = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except Exception as e:
        log(f"  [錯誤] 無法讀取：{e}"); return

    img_cv = detect_and_resize(img_cv)
    img_h, img_w = img_cv.shape[:2]
    boxes = detect_red_boxes(img_cv)
    log(f"  偵測到 {len(boxes)} 個對話框")

    if not boxes:
        log("  [警告] 沒有偵測到紅框"); return

    text_layers = []

    for i, box in enumerate(boxes):
        x, y, w, h = box['x'], box['y'], box['w'], box['h']
        cx, cy = find_green_center(img_cv, box)
        log(f"  [框{i+1}] ({x},{y}) {w}x{h}")
        try:
            text = clean_api_text(call_api_for_text(img_cv, box, api_key))
            log(f"    文字：「{text}」")
        except Exception as e:
            log(f"    [API錯誤] {e}"); text = f"框{i+1}文字"

        is_placeholder = any(kw in text for kw in ['框內空白', '無文字', '不清晰', '無可辨識', '圖中'])
        if not text or is_placeholder or w < 50 or h < 50:
            log(f"    [略過] 空白或殘留框")
        else:
            text_layers.append({'text': text, 'cx': cx, 'cy': cy,
                                 'box_w': w, 'box_h': h, 'label': re.sub(r'[\n\r:：]', '', text[:4])})
        time.sleep(0.3)

    generate_jsx(text_layers, img_w, img_h, output_jsx, input_path.name,
                 font_name, font_size, chars_per_col, line_spacing,
                 tracking, white_stroke, stroke_size,
                 auto_size, fs_min, fs_max)
    log(f"  ✅ 完成！{len(text_layers)} 個文字圖層 → {output_jsx.name}")

# ══════════════════════════════════════════════
#  GUI 面板
# ══════════════════════════════════════════════

class RedboxApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("換對白工具")
        self.configure(bg=BG_COLOR)
        self.resizable(False, False)
        self._build_ui()

    # ── 輔助：卡片容器 ──
    def _card(self, parent, title, color):
        outer = tk.Frame(parent, bg=color, bd=0)
        tk.Label(outer, text=title, bg=color, fg=TEXT_LIGHT,
                 font=("Microsoft JhengHei", 10, "bold")).pack(anchor="w", padx=10, pady=(6, 2))
        inner = tk.Frame(outer, bg=color)
        inner.pack(fill="x", padx=10, pady=(0, 8))
        return outer, inner

    # ── 輔助：參數列 ──
    def _prow(self, parent, bg, label, var, width=6):
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, bg=bg, fg=TEXT_LIGHT,
                 font=("Microsoft JhengHei", 9), width=14, anchor="w").pack(side="left")
        e = tk.Entry(row, textvariable=var, width=width,
                     bg=INPUT_BG, fg=INPUT_FG, insertbackground=INPUT_FG,
                     relief="flat", font=("Consolas", 9))
        e.pack(side="left", padx=4)
        return row

    # ── 輔助：按鈕 ──
    def _btn(self, parent, bg, text, fg, cmd, padx=(0, 0)):
        b = tk.Button(parent, text=text, bg=bg, fg=fg, activebackground=fg,
                      activeforeground=bg, relief="flat", bd=0, cursor="hand2",
                      font=("Microsoft JhengHei", 9, "bold"),
                      padx=10, pady=4, command=cmd)
        b.pack(side="left", padx=padx)
        return b

    def _build_ui(self):
        # ── 標題列 ──
        hdr = tk.Frame(self, bg=HEADER_BG)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🛠  換對白工具", bg=HEADER_BG, fg=ACCENT,
                 font=("Microsoft JhengHei", 13, "bold")).pack(side="left", padx=14, pady=8)
        tk.Label(hdr, text="YUCHIN LIN", bg=HEADER_BG, fg=TEXT_DIM,
                 font=("Microsoft JhengHei", 8)).pack(side="right", padx=14)

        body = tk.Frame(self, bg=BG_COLOR)
        body.pack(fill="both", padx=12, pady=8)

        # ── 路徑設定 ──
        path_frm, path_inner = self._card(body, "路徑設定", PANEL_COLOR)
        path_frm.pack(fill="x", pady=(0, 6))

        self.input_var  = tk.StringVar()
        self.output_var = tk.StringVar()

        def path_row(parent, label, var, pick_cmd):
            row = tk.Frame(parent, bg=PANEL_COLOR)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=PANEL_COLOR, fg=TEXT_LIGHT,
                     font=("Microsoft JhengHei", 9), width=5, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=var, bg=INPUT_BG, fg=INPUT_FG,
                     insertbackground=INPUT_FG, relief="flat",
                     font=("Consolas", 8), width=32).pack(side="left", padx=4)
            tk.Button(row, text="瀏覽", bg=ACCENT, fg="white", relief="flat",
                      bd=0, cursor="hand2", font=("Microsoft JhengHei", 8),
                      padx=6, pady=2, command=pick_cmd).pack(side="left")

        path_row(path_inner, "輸入", self.input_var,  self._pick_input)
        path_row(path_inner, "輸出", self.output_var, self._pick_output)

        # ── 處理模式 ──
        self.mode_var = tk.StringVar(value="single")
        mode_row = tk.Frame(path_inner, bg=PANEL_COLOR)
        mode_row.pack(fill="x", pady=(4, 0))
        tk.Label(mode_row, text="模式", bg=PANEL_COLOR, fg=TEXT_LIGHT,
                 font=("Microsoft JhengHei", 9), width=5, anchor="w").pack(side="left")
        for val, txt in [("single", "● 單張"), ("batch", "⚡ 批量")]:
            tk.Radiobutton(mode_row, text=txt, variable=self.mode_var, value=val,
                           bg=PANEL_COLOR, fg=TEXT_LIGHT, selectcolor=INPUT_BG,
                           activebackground=PANEL_COLOR, activeforeground=TEXT_LIGHT,
                           font=("Microsoft JhengHei", 9)).pack(side="left", padx=6)

        # ── API金鑰 ──
        api_frm, api_inner = self._card(body, "API 金鑰", PANEL_COLOR)
        api_frm.pack(fill="x", pady=(0, 6))
        self.api_var = tk.StringVar(value="sk-ant-xxxxxxxx")
        api_row = tk.Frame(api_inner, bg=PANEL_COLOR)
        api_row.pack(fill="x")
        tk.Entry(api_row, textvariable=self.api_var, bg=INPUT_BG, fg=INPUT_FG,
                 insertbackground=INPUT_FG, relief="flat",
                 font=("Consolas", 8), width=44, show="*").pack(side="left", padx=(0,4))
        self.show_key = tk.BooleanVar(value=False)
        def toggle_key():
            entries = [w for w in api_row.winfo_children() if isinstance(w, tk.Entry)]
            if entries:
                entries[0].config(show="" if self.show_key.get() else "*")
        tk.Checkbutton(api_row, text="顯示", variable=self.show_key,
                       bg=PANEL_COLOR, fg=TEXT_DIM, selectcolor=INPUT_BG,
                       activebackground=PANEL_COLOR, command=toggle_key,
                       font=("Microsoft JhengHei", 8)).pack(side="left")

        # ── 字體設定 ──
        font_frm, font_inner = self._card(body, "字體設定", "#f9f1ea")
        font_frm.pack(fill="x", pady=(0, 6))

        # 字體名稱
        fn_row = tk.Frame(font_inner, bg="#f9f1ea")
        fn_row.pack(fill="x", pady=2)
        tk.Label(fn_row, text="字體名稱", bg="#f9f1ea", fg=TEXT_LIGHT,
                 font=("Microsoft JhengHei", 9), width=10, anchor="w").pack(side="left")
        self.font_var = tk.StringVar(value="SourceHanSansTC-Regular")
        font_combo = ttk.Combobox(fn_row, textvariable=self.font_var, width=28,
                                   font=("Consolas", 8))
        font_combo['values'] = [
            "SourceHanSansTC-Regular",
            "SourceHanSerifTC-Regular",
            "MingLiU",
            "PMingLiU",
            "KaiTi",
            "DFKaiShu-SB-Estd-BF",
            "NotoSerifCJKtc-Regular",
        ]
        font_combo.pack(side="left", padx=4)

        # 數值參數
        self.fontsize_var   = tk.IntVar(value=16)
        self.linesp_var     = tk.IntVar(value=20)
        self.tracking_var   = tk.IntVar(value=0)
        self.charcol_var    = tk.IntVar(value=7)

        self._prow(font_inner, "#f9f1ea", "字級 (pt)",    self.fontsize_var)
        self._prow(font_inner, "#f9f1ea", "行距 (pt)",    self.linesp_var)
        self._prow(font_inner, "#f9f1ea", "字距 (1/1000em)", self.tracking_var)
        self._prow(font_inner, "#f9f1ea", "每欄字數",     self.charcol_var)

        # 白邊設定
        self.stroke_var      = tk.BooleanVar(value=False)
        self.stroke_size_var = tk.IntVar(value=3)

        stroke_row = tk.Frame(font_inner, bg="#f9f1ea")
        stroke_row.pack(fill="x", pady=2)
        tk.Checkbutton(stroke_row, text="字體包白邊", variable=self.stroke_var,
                       bg="#f9f1ea", fg=TEXT_LIGHT, selectcolor=INPUT_BG,
                       activebackground="#f9f1ea",
                       font=("Microsoft JhengHei", 9)).pack(side="left")

        tk.Label(stroke_row, text="白邊厚度(px)", bg="#f9f1ea", fg=TEXT_DIM,
                 font=("Microsoft JhengHei", 9)).pack(side="left", padx=(12, 4))
        tk.Entry(stroke_row, textvariable=self.stroke_size_var, width=4,
                 bg=INPUT_BG, fg=INPUT_FG, insertbackground=INPUT_FG,
                 relief="flat", font=("Consolas", 9)).pack(side="left")

        # 自動字級（依每框大小＋字數現算級數；關掉＝全頁用上面的固定字級）
        self.autosize_var = tk.BooleanVar(value=True)
        self.fsmin_var    = tk.IntVar(value=24)
        self.fsmax_var    = tk.IntVar(value=200)

        auto_row = tk.Frame(font_inner, bg="#f9f1ea")
        auto_row.pack(fill="x", pady=2)
        tk.Checkbutton(auto_row, text="自動字級(依框大小)", variable=self.autosize_var,
                       bg="#f9f1ea", fg=TEXT_LIGHT, selectcolor=INPUT_BG,
                       activebackground="#f9f1ea",
                       font=("Microsoft JhengHei", 9)).pack(side="left")
        tk.Label(auto_row, text="最小(px)", bg="#f9f1ea", fg=TEXT_DIM,
                 font=("Microsoft JhengHei", 9)).pack(side="left", padx=(12, 4))
        tk.Entry(auto_row, textvariable=self.fsmin_var, width=4,
                 bg=INPUT_BG, fg=INPUT_FG, insertbackground=INPUT_FG,
                 relief="flat", font=("Consolas", 9)).pack(side="left")
        tk.Label(auto_row, text="最大(px)", bg="#f9f1ea", fg=TEXT_DIM,
                 font=("Microsoft JhengHei", 9)).pack(side="left", padx=(8, 4))
        tk.Entry(auto_row, textvariable=self.fsmax_var, width=4,
                 bg=INPUT_BG, fg=INPUT_FG, insertbackground=INPUT_FG,
                 relief="flat", font=("Consolas", 9)).pack(side="left")

        # ── 海外版（英文） ──
        EN_BG = "#eaf1f8"   # 海外版卡片＝淺藍
        en_frm, en_inner = self._card(body, "🌏 海外版（英文／越南文）", EN_BG)
        en_frm.pack(fill="x", pady=(0, 6))

        lg_row = tk.Frame(en_inner, bg=EN_BG)
        lg_row.pack(fill="x", pady=2)
        tk.Label(lg_row, text="翻譯成", bg=EN_BG, fg=TEXT_LIGHT,
                 font=("Microsoft JhengHei", 9), width=10, anchor="w").pack(side="left")
        self.lang_var = tk.StringVar(value="英文")
        lang_combo = ttk.Combobox(lg_row, textvariable=self.lang_var, width=10,
                                  values=["英文", "越南文"], state="readonly",
                                  font=("Microsoft JhengHei", 9))
        lang_combo.pack(side="left", padx=4)
        lang_combo.bind("<<ComboboxSelected>>", lambda e: self._on_lang_change())

        ef_row = tk.Frame(en_inner, bg=EN_BG)
        ef_row.pack(fill="x", pady=2)
        tk.Label(ef_row, text="字體", bg=EN_BG, fg=TEXT_LIGHT,
                 font=("Microsoft JhengHei", 9), width=10, anchor="w").pack(side="left")
        self.en_font_var = tk.StringVar(value="ComicNeue-Bold")   # Regular 太細，預設用 Bold
        en_font_combo = ttk.Combobox(ef_row, textvariable=self.en_font_var, width=28,
                                     font=("Consolas", 8))
        en_font_combo['values'] = [
            "ComicNeue-Bold",         # 一般對白主力（預設；粗度夠螢幕清楚）
            "ComicNeue-Regular",      # 細版，印刷大開本可用
            "Bangers-Regular",        # 大喊/吼叫/音效
            "LuckiestGuy-Regular",    # 超粗，爆炸性台詞
            "PatrickHand-Regular",    # 手寫感，內心話
            "ComicSansMS",            # Windows 內建備用
        ]
        en_font_combo.pack(side="left", padx=4)
        self.en_font_combo = en_font_combo
        self.en_font_values = list(en_font_combo['values'])
        self.lang_hint = tk.Label(en_inner, text="", bg=EN_BG, fg="#b3541e",
                                  font=("Microsoft JhengHei", 8), anchor="w", justify="left")
        self.lang_hint.pack(fill="x")

        eo_row = tk.Frame(en_inner, bg=EN_BG)
        eo_row.pack(fill="x", pady=2)
        self.en_upper_var = tk.BooleanVar(value=True)
        tk.Checkbutton(eo_row, text="全大寫（英文慣例；越南文預設關閉，聲調符號較清楚）", variable=self.en_upper_var,
                       bg=EN_BG, fg=TEXT_LIGHT, selectcolor=INPUT_BG,
                       activebackground=EN_BG, activeforeground=TEXT_LIGHT,
                       font=("Microsoft JhengHei", 9)).pack(side="left")
        self.en_stroke_var = tk.BooleanVar(value=True)
        tk.Checkbutton(eo_row, text="字包白邊", variable=self.en_stroke_var,
                       bg=EN_BG, fg=TEXT_LIGHT, selectcolor=INPUT_BG,
                       activebackground=EN_BG, activeforeground=TEXT_LIGHT,
                       font=("Microsoft JhengHei", 9)).pack(side="left", padx=(12, 0))

        eb_row = tk.Frame(en_inner, bg=EN_BG)
        eb_row.pack(fill="x", pady=(4, 2))
        self.en_b1 = self._btn(eb_row, "#4a7fb5", "① 輸出對白稿(中→英草稿)", "white",
                  lambda: self._run_en(1), padx=(0, 6))
        self.en_b2 = self._btn(eb_row, "#33a184", "② 英文上字(讀對白稿)", "white",
                  lambda: self._run_en(2), padx=(0, 6))

        tk.Label(en_inner, text="紅框=對白、藍框(純藍#0000FF)=手寫狀聲字(自動用大喊字體小字註記在綠X處)\n流程：① 產對白稿 txt → 打開審譯文 → 存檔 → ②（白邊厚度/最小最大字級沿用上面設定）",
                 bg=EN_BG, fg=TEXT_DIM, font=("Microsoft JhengHei", 8),
                 anchor="w", justify="left").pack(fill="x", pady=(2, 0))

        # ── 執行按鈕 ──
        btn_row = tk.Frame(body, bg=BG_COLOR)
        btn_row.pack(fill="x", pady=(4, 0))
        self._btn(btn_row, ACCENT, "▶ 執行(直接上字)", "white", self._run, padx=(0, 6))
        self._btn(btn_row, "#33a184", "② 中文上字(讀對白稿)", "white",
                  self._run_zh2, padx=(0, 6))
        tk.Label(btn_row, text="← txt「中文：」行校完錯字按這顆", bg=BG_COLOR,
                 fg=TEXT_DIM, font=("Microsoft JhengHei", 8)).pack(side="left")

        # ── 執行紀錄 ──
        log_frm = tk.LabelFrame(body, text=" 執行紀錄 ", bg=BG_COLOR, fg=TEXT_DIM,
                                 font=("Microsoft JhengHei", 8), bd=1, relief="groove")
        log_frm.pack(fill="both", pady=(8, 0))
        self.log_box = tk.Text(log_frm, height=10, bg="#fbfdfc", fg="#1f7a55",
                               font=("Consolas", 8), relief="flat",
                               state="disabled", wrap="word")
        sb = tk.Scrollbar(log_frm, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_box.pack(fill="both", padx=4, pady=4)

        self._log("就緒，請設定路徑後執行")

    # ── 路徑選擇 ──
    def _pick_input(self):
        mode = self.mode_var.get()
        if mode == "batch":
            p = filedialog.askdirectory(title="選擇輸入資料夾")
        else:
            p = filedialog.askopenfilename(
                title="選擇輸入圖片",
                filetypes=[("圖片", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"), ("全部", "*.*")]
            )
        if p:
            self.input_var.set(p)

    def _pick_output(self):
        p = filedialog.askdirectory(title="選擇輸出資料夾")
        if p:
            self.output_var.set(p)

    # ── 執行 ──
    def _run(self):
        input_path  = self.input_var.get().strip()
        output_dir  = self.output_var.get().strip()
        api_key     = self.api_var.get().strip()

        if not input_path:
            messagebox.showwarning("提醒", "請先選擇輸入路徑"); return
        if not output_dir:
            messagebox.showwarning("提醒", "請先選擇輸出資料夾"); return
        if not api_key or api_key == "sk-ant-xxxxxxxx":
            messagebox.showwarning("提醒", "請輸入有效的 API 金鑰"); return

        font_name   = self.font_var.get().strip()
        font_size   = self.fontsize_var.get()
        line_spacing= self.linesp_var.get()
        tracking    = self.tracking_var.get()
        chars_col   = self.charcol_var.get()
        white_stroke= self.stroke_var.get()
        stroke_size = self.stroke_size_var.get()
        auto_size   = self.autosize_var.get()
        fs_min      = self.fsmin_var.get()
        fs_max      = self.fsmax_var.get()
        mode        = self.mode_var.get()

        # 防連按：這個功能會呼叫付費 API，手滑點兩下＝重複扣錢，跑完前一律擋下
        if getattr(self, "_busy", False):
            self._log("⚠ 已有任務在執行中，請等它完成再按"); return

        def worker():
            try:
                if mode == "single":
                    if not Path(input_path).is_file():
                        self._log("⚠ 單張模式請選圖片檔案"); return
                    process_page(input_path, output_dir, api_key,
                                 font_name, font_size, chars_col, line_spacing,
                                 tracking, white_stroke, stroke_size, self._log,
                                 auto_size, fs_min, fs_max)
                else:
                    p = Path(input_path)
                    if not p.is_dir():
                        self._log("⚠ 批量模式請選資料夾"); return
                    exts = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp'}
                    files = sorted([f for f in p.iterdir() if f.suffix.lower() in exts])
                    if not files:
                        self._log("⚠ 資料夾內沒有圖片"); return
                    self._log(f"找到 {len(files)} 張，開始批量...")
                    for i, f in enumerate(files, 1):
                        self._log(f"[{i}/{len(files)}] {f.name}")
                        process_page(str(f), output_dir, api_key,
                                     font_name, font_size, chars_col, line_spacing,
                                     tracking, white_stroke, stroke_size, self._log,
                                     auto_size, fs_min, fs_max)
                        time.sleep(0.5)
                    self._log("✅ 全部完成！")
            except Exception as e:
                self._log(f"❌ 錯誤：{e}")
            finally:
                self._busy = False   # 不管成功失敗，跑完一定解鎖

        self._busy = True
        threading.Thread(target=worker, daemon=True).start()

    # ── 中文②：讀校完錯字的對白稿上中文字（不用 API、免費） ──
    def _run_zh2(self):
        input_path = self.input_var.get().strip()
        output_dir = self.output_var.get().strip()
        if not input_path:
            messagebox.showwarning("提醒", "請先選擇輸入路徑"); return
        if not output_dir:
            messagebox.showwarning("提醒", "請先選擇輸出資料夾"); return

        font_name    = self.font_var.get().strip()
        font_size    = self.fontsize_var.get()
        line_spacing = self.linesp_var.get()
        tracking     = self.tracking_var.get()
        chars_col    = self.charcol_var.get()
        white_stroke = self.stroke_var.get()
        stroke_size  = self.stroke_size_var.get()
        auto_size    = self.autosize_var.get()
        fs_min       = self.fsmin_var.get()
        fs_max       = self.fsmax_var.get()
        mode         = self.mode_var.get()

        if getattr(self, "_busy", False):
            self._log("⚠ 已有任務在執行中，請等它完成再按"); return

        def run_one(path):
            process_page_chinese(path, output_dir, font_name, font_size, chars_col,
                                 line_spacing, tracking, white_stroke, stroke_size,
                                 self._log, auto_size, fs_min, fs_max)

        def worker():
            try:
                if mode == "single":
                    if not Path(input_path).is_file():
                        self._log("⚠ 單張模式請選圖片檔案"); return
                    run_one(input_path)
                else:
                    p = Path(input_path)
                    if not p.is_dir():
                        self._log("⚠ 批量模式請選資料夾"); return
                    exts = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp'}
                    files = sorted([f for f in p.iterdir() if f.suffix.lower() in exts])
                    if not files:
                        self._log("⚠ 資料夾內沒有圖片"); return
                    self._log(f"找到 {len(files)} 張，開始批量...")
                    for i, f in enumerate(files, 1):
                        self._log(f"[{i}/{len(files)}] {f.name}")
                        run_one(str(f))
                    self._log("✅ 全部完成！")
            except Exception as e:
                self._log(f"❌ 錯誤：{e}")
            finally:
                self._busy = False

        self._busy = True
        threading.Thread(target=worker, daemon=True).start()

    def _on_lang_change(self):
        """切換翻譯語言：連動預設字體、全大寫、按鈕文字與提示"""
        code = 'vi' if self.lang_var.get() == '越南文' else 'en'
        cfg = LANGS[code]
        zh = cfg['zh']
        self.en_font_var.set(cfg['font'])
        self.en_upper_var.set(cfg['upper'])
        self.en_font_combo['values'] = (["PatrickHand-Regular", "Bangers-Regular"]
                                        if code == 'vi' else self.en_font_values)
        self.en_b1.config(text=f"① 輸出對白稿(中→{zh}草稿)")
        self.en_b2.config(text=f"② {zh}上字(讀對白稿)")
        self.lang_hint.config(text=(
            "越南文要用「含聲調符號」的字體：Patrick Hand、Bangers 有支援；\n"
            "其他字體（含 Comic Neue）請先在 PS 打一段越南文確認聲調有顯示。"
            if code == 'vi' else ""))

    # ── 海外版執行（stage 1=輸出對白稿、2=英文／越南文上字） ──
    def _run_en(self, stage):
        input_path = self.input_var.get().strip()
        output_dir = self.output_var.get().strip()
        api_key    = self.api_var.get().strip()

        if not input_path:
            messagebox.showwarning("提醒", "請先選擇輸入路徑"); return
        if not output_dir:
            messagebox.showwarning("提醒", "請先選擇輸出資料夾"); return
        if stage == 1 and (not api_key or api_key == "sk-ant-xxxxxxxx"):
            messagebox.showwarning("提醒", "「① 輸出對白稿」需要 API 金鑰（辨識＋翻譯）"); return

        lang         = 'vi' if self.lang_var.get() == '越南文' else 'en'
        font_name    = self.en_font_var.get().strip()
        uppercase    = self.en_upper_var.get()
        white_stroke = self.en_stroke_var.get()   # 英文自己的白邊開關（預設開）
        stroke_size  = self.stroke_size_var.get()
        fs_min       = self.fsmin_var.get()
        fs_max       = self.fsmax_var.get()
        mode         = self.mode_var.get()

        # 防連按：①會呼叫付費 API，跟中文流程共用同一把鎖
        if getattr(self, "_busy", False):
            self._log("⚠ 已有任務在執行中，請等它完成再按"); return

        if stage == 2 and lang == 'vi' and font_name.lower().startswith('comicneue'):
            self._log("⚠ 目前字體是 Comic Neue，越南文聲調可能顯示不全，建議改用 PatrickHand-Regular")

        def run_one(path):
            if stage == 1:
                process_page_dialogue_export(path, output_dir, api_key, self._log, lang)
            else:
                process_page_english(path, output_dir, font_name, uppercase,
                                     white_stroke, stroke_size, self._log,
                                     fs_min, fs_max, lang)

        def worker():
            try:
                if mode == "single":
                    if not Path(input_path).is_file():
                        self._log("⚠ 單張模式請選圖片檔案"); return
                    run_one(input_path)
                else:
                    p = Path(input_path)
                    if not p.is_dir():
                        self._log("⚠ 批量模式請選資料夾"); return
                    exts = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp'}
                    files = sorted([f for f in p.iterdir() if f.suffix.lower() in exts])
                    if not files:
                        self._log("⚠ 資料夾內沒有圖片"); return
                    self._log(f"找到 {len(files)} 張，開始批量...")
                    for i, f in enumerate(files, 1):
                        self._log(f"[{i}/{len(files)}] {f.name}")
                        run_one(str(f))
                        if stage == 1:
                            time.sleep(0.5)
                    self._log("✅ 全部完成！")
            except Exception as e:
                self._log(f"❌ 錯誤：{e}")
            finally:
                self._busy = False

        self._busy = True
        threading.Thread(target=worker, daemon=True).start()

    def _log(self, msg):
        def _do():
            self.log_box.config(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, _do)

# ══════════════════════════════════════════════
#  啟動
# ══════════════════════════════════════════════

if __name__ == "__main__":
    app = RedboxApp()
    app.mainloop()
