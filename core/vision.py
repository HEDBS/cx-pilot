# -*- coding: utf-8 -*-
"""识图链（B6）：题图 -> 文本，真实用户侧口径。

优先级（可插拔降级）：
  1. 本地 OCR（rapidocr-onnxruntime，零联网零成本；富文本题面识别率高）
  2. 视图 API（用户在设置里配的任意 OpenAI 兼容多模态端点，vision_providers）
  3. 全挂 -> image_flag 保持懒标记，UI 显示原图走人工填写

OCR 结果矩阵二维排版不保证还原（用户明确：不求完美，80% 富文本+cmd 截图能读即可）。
vendor 目录（项目根目录 vendor/）里有 rapidocr 时自动启用，无需全局安装。
"""
import os
import re
import sys
import json
import base64
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(BASE, "vendor")
if os.path.isdir(VENDOR) and VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

LAST_ERROR = [""]           # 最近一次 vision 失败原因（UI 可展示，别静默吞）
_ocr_engine = None          # 懒加载单例
_ocr_failed = False


def _get_engine():
    """返回 RapidOCR 实例；不可用返回 None（只尝试一次）。"""
    global _ocr_engine, _ocr_failed
    if _ocr_engine is not None or _ocr_failed:
        return _ocr_engine
    try:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    except Exception:
        _ocr_failed = True
        _ocr_engine = None
    return _ocr_engine


def ocr_image(path_or_bytes):
    """本地 OCR：返回按 y 排序拼接的文本（失败返回 None）。"""
    eng = _get_engine()
    if eng is None:
        return None
    try:
        result, _ = eng(path_or_bytes)
    except Exception:
        return None
    if not result:
        return ""
    # 每行 (box, text, score)；按行首 y 排序，同行按 x
    rows = sorted(result, key=lambda r: (round(r[0][0][1] / 12), r[0][0][0]))
    return "\n".join(str(r[1]).strip() for r in rows if str(r[1]).strip())


# ---------- 视图 API（多模态 OpenAI 兼容） ----------
def vision_api(image_bytes: bytes, cfg: dict, keys: dict, hint=""):
    """按用户配置的 vision provider 读图。走 pv.chat 统一通道（UA/代理/重试共享）。
    返回 (text, conf)；失败 (None, 0)，原因在 LAST_ERROR。"""
    b64 = base64.b64encode(image_bytes).decode()
    prompt = ("转写图片中的题目文字（含公式用线性记法，矩阵按行 空格分列 换行分排）。"
              + (hint or "") + " 只输出转写内容本身，不要分析过程。")
    try:
        from core import providers as pv
    except ImportError:
        sys.path.insert(0, os.path.join(BASE, ".."))
        from core import providers as pv
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]
    # 转写缓存：推理模型 80-200s/张，同图同模型只转一次（重跑/批量秒出）
    import hashlib
    cache_p = os.path.join(BASE, "data", "vision_cache.json")
    ck = hashlib.sha1(image_bytes + cfg.get("model", "").encode()).hexdigest()[:24]
    try:
        cache = json.load(open(cache_p, encoding="utf-8"))
    except Exception:
        cache = {}
    if ck in cache:
        return cache[ck], 0.85
    try:
        txt = pv.chat(cfg, keys, [{"role": "user", "content": content}], max_tokens=800, timeout=280)
        txt = (txt or "").strip()
        if not txt:
            LAST_ERROR[0] = "空响应"
            return None, 0
        # nemotron 类推理模型会把独白吐回来：抠最后一段正文（启发式：含「转写/题目/设」的行优先）
        if txt.startswith(("The user", "I need", "Let me", "首先", "好的")):
            keep = [ln for ln in txt.splitlines()
                    if not re.match(r"^(The user|I need|Let me|\*\*[0-9])", ln)]
            txt = "\n".join(keep).strip() or txt
        cache[ck] = txt
        try:
            json.dump(cache, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
        return txt, 0.85
    except Exception as e:
        LAST_ERROR[0] = str(e)[:200]
        return None, 0


# ---------- 降级主入口 ----------
def resolve_image_text(image_bytes: bytes, settings: dict = None, keys: dict = None,
                       hint: str = ""):
    """返回 (text, source)；source in ocr|vision_api|none。"""
    t = ocr_image(image_bytes)
    if t and len(t) >= 8:
        return t, "ocr"
    vp = (settings or {}).get("vision_providers") or []
    for cfg in vp:
        if not cfg.get("enabled"):
            continue
        if cfg.get("kind") == "openai_compat" and cfg.get("base_url") and cfg.get("model"):
            txt, conf = vision_api(image_bytes, cfg, keys or {}, hint)
            if txt:
                return txt, "vision_api"
    return None, "none"


def download_image(client, url):
    """题图下载（ananas CDN；带 UA+Referer 更稳）。"""
    if url.startswith("//"):
        url = "https:" + url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": client.UA,
                                                   "Referer": "https://mooc1.chaoxing.com/"})
        return client.op.open(req, timeout=30).read()
    except Exception:
        return None
