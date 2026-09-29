# -*- coding: utf-8 -*-
"""solver：题队列调度（provider 路由+重试+限速+留痕）。
解题逻辑本身暂以「选择题/判断题 直答」为主，填空题走文本生成；
provider 链按 settings.providers 顺序，失败自动降级到下一家。"""
import datetime
import json
import os
import random
import re
import time

from core import providers as pv
from core import vision  # B6a 识图链（vendor sys.path 只在本家 vision.py 顶部处理）

from core.client import DATA  # 统一数据目录


class Job:
    """一份作业的解题任务。"""

    def __init__(self, work_ref, questions):
        self.work_ref = work_ref          # dict: course/title/workId/answerId...
        self.questions = questions        # list[dict]: qid/type/stem/options/...
        self.results = {}                 # qid -> {answer,confidence,source,status}
        self.state = "init"               # init|running|done|partial|failed
        self.started = None

    def pending(self):
        return [q for q in self.questions
                if self.results.get(q["qid"], {}).get("status") not in ("ok",)]

    def progress(self):
        return "%d/%d" % (len(self.questions) - len(self.pending()), len(self.questions))


# ---------- B6a 识图链辅助 ----------
_BOILER = re.compile(r"填空题|单选题|多选题|判断题|主观题|分\)?$|[\(（]\s*共?\d")

# ---------- 填空/多选/简答的作答风格令 ----------
# 用户要求（2026-09-29）：简答一类的答案要「简洁、语言平实、去 AI 味、像学生写出来的」。
# 为什么必须写进提示词：模型默认输出「首先…其次…综上所述」+ 分点 + 加粗 + 铺陈，
# 一眼就不是学生手写；学习通的简答输入框也根本不需要那个长度。
_TEXT_RULES = (
    "作答规则（逐条遵守）：\n"
    "① 只输出答案本身。不要开场白、不要收尾总结、不要解释你在做什么，"
    "不要任何 markdown 标记（**、##、- 列表）、不要 emoji。\n"
    "② 像学生自己手写的答案：句子短，直说结论和理由，可以用「因为…所以…」"
    "「也就是说」这类直白说法。\n"
    "③ 禁用这类套话：首先/其次/再次/最后、综上所述/总而言之、值得注意的是、"
    "在一定程度上、具有重要意义、有效地、从而、进而、随着……的发展。\n"
    "④ 简洁优先：一句话能答清就一句话，别为显得全面而分点罗列或排比铺陈。\n"
    "⑤ 要能手写成纯文本：矩阵按行空格分列、换行分排；分数写成 9/2 这种。\n"
)


def _text_prompt(q):
    """填空/多选/简答的提示词（按题型分别收紧"最短答案"的约束）。"""
    n = int(q.get("blank_count") or 1)
    t = q.get("type") or ""
    if t == "blank":
        task = ("这是填空题，共 %d 空。每空只写答案本身（一个词/一个数/一个式子），"
                "不要写成句子、不要解释；每行一个答案，按顺序对应每个空。" % n)
    else:
        # 注意：multi 不走这里（多选在 solver 里走 pv.solve_multi 出字母串，
        # 提交器按 answertype=1 发答案{qid}=字母；丢给文本生成会回来一整句话）
        task = ("这是简答题。用平实的话直接回答，写成一小段就行，"
                "不要小标题、不要分点罗列、不要「首先其次」。")
    return "%s%s\n\n题目：\n%s" % (_TEXT_RULES, task, q.get("stem") or "")


def _stem_needs_image(stem):
    """实义字符判定：剥掉题型 boilerplate（「(填空题, 16.6分)」这种模板字）后
    不足 8 个实义字符 → 题干实质内容在图里，走识图。
    （旧版阈值 4 且没剥 boilerplate：'填空题166分'=7 字符 >4 → 图片题漏进文本模型，
    模型拿"(填空题,16.6分)"当题面回答「请把题目发给我」，实测泄漏成 ok 答案。）"""
    t = re.sub(r"\(\s*(填空题|单选题|多选题|判断题|主观题)\s*,?\s*\d+(\.\d+)?分\s*\)", "", stem or "")
    t = re.sub(r"（?矩阵写法同前）?", "", t)
    core = re.sub(r"[^0-9A-Za-z一-鿿]", "", t)
    return len(core) < 8


_REFUSAL = re.compile(r"(看不到|没看到|无法确定|无法回答|请提供|请把|请您提供|需要看到|没有.{0,6}(题目|内容|题干)|抱歉|sorry|i cannot|i need)", re.I)

def _is_refusal(txt):
    return bool(_REFUSAL.search(txt or ""))


def _ocr_dirty(text):
    """OCR 结果公式损伤启发式（实测 |A|→4 这类）：
    ① |/｜（行列式/矩阵竖线被读成绝对值）② 上标残留（²³¹、^n、A2 字母紧跟孤数字）
    ③ 纯数字孤行占比 >40%（矩阵被读散）。命中任一 → 直信 OCR 会错答，转 vision_api。"""
    if re.search(r"[|｜]", text or ""):
        return True
    if re.search(r"[²³¹⁰⁴-⁹]|\^[\dA-Za-z]", text):
        return True
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]\d(?![A-Za-z0-9])", text):
        return True
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if lines:
        n_num = sum(1 for ln in lines if re.fullmatch(r"[\d\s.,;+\-*/=()（）|｜]+", ln))
        if n_num / float(len(lines)) > 0.3:
            return True
    # ④ B6 三测漏网根因（405923767：「2 -1 5 设 A= B 则…」判干净直进文本模型→垃圾答）：
    #    矩阵题干被 OCR 读散成缺括号碎片——「设 X=」存在但没有任何 (数字 开头的矩阵体
    if re.search(r"设\s*[A-Za-z]\s*=", text or "") and not re.search(r"[(（]\s*[-\d]", text or ""):
        return True
    return False


def _vision_fallback(data, settings, keys):
    """OCR 判脏后逐个试已启用的 vision provider（口径同 vision.resolve_image_text）。"""
    for cfg in settings.get("vision_providers") or []:
        if not cfg.get("enabled"):
            continue
        if cfg.get("kind") == "openai_compat" and cfg.get("base_url") and cfg.get("model"):
            txt, _conf = vision.vision_api(data, cfg, keys or {})
            if txt:
                return txt, "vision_api"
    return None, "none"


def _image_to_text(q, stem, client, settings, keys, on_event):
    """image_flag 题识图。返回 (text, source)，source in ocr|vision_api|none。
    none=链全挂（下载失败/无图/OCR空且vision挂），调用方据此标 need_manual。"""
    urls = q.get("img_urls") or []
    if not urls:
        return None, "none"
    data = vision.download_image(client, urls[0])
    if not data:
        if on_event: on_event("warn", "%s 题图下载失败" % q["qid"])
        return None, "none"
    text, source = vision.resolve_image_text(data, settings, keys)
    if source == "ocr" and _ocr_dirty(text):
        if on_event: on_event("warn", "%s OCR疑似公式损伤，转视图API" % q["qid"])
        t2, s2 = _vision_fallback(data, settings, keys)
        return (t2, s2) if s2 == "vision_api" else (None, "none")
    return text, source


def solve_job(job, on_event=None, settings=None, keys=None, client=None):
    """顺序解一题题（API 并发留给 P3 后期做）。返回 job。
    B6a：client 由调用方传入（默认 None→跳过图片链，保持旧行为）。"""
    settings = settings or pv.load_settings()
    keys = keys if keys is not None else pv.load_keys()
    chain = pv.active_providers(settings, keys)
    if not chain:
        job.state = "failed"
        if on_event: on_event("error", "没有可用的解题后端：先到设置里启用一个 provider")
        return job
    if on_event:
        on_event("start", "后端链: %s" % " > ".join(c["name"] for c in chain))
    job.state = "running"
    job.started = datetime.datetime.now().isoformat(timespec="seconds")
    rl = settings.get("rate_limit_s", [1.0, 2.5])
    for q in job.questions:
        res = {"qid": q["qid"], "type": q["type"], "status": "failed",
               "answer": "", "confidence": 0.0, "source": "", "tries": 0}
        stem = q.get("stem", "")
        img_cap = None
        # ---- B6a 识图接线：image_flag 且题干无有效文字 ----
        if client is not None and q.get("image_flag") and _stem_needs_image(stem):
            text, source = _image_to_text(q, stem, client, settings, keys, on_event)
            if source in ("ocr", "vision_api"):
                q["_img_source"] = source          # 留痕（题卡/日志可查来源）
                stem = (stem + "\n" if stem.strip() else "") + text
                img_cap = 0.6 if source == "ocr" else 0.75   # 进既有阈值闸门
                if on_event:
                    on_event("q", "%s 识图(%s): %s" % (q["qid"], source, text[:24].replace("\n", " ")))
            else:
                # 全挂 → 人工，不硬答（审批屏出「需人工」卡+原图）
                res.update(status="need_manual", need_manual=True)
                job.results[q["qid"]] = res
                if on_event:
                    on_event("q", "%s %s -> 需人工（识图链全挂）" % (q["type"], q["qid"]))
                continue
        for cfg in chain:
            for attempt in (1, 2):
                try:
                    if q["type"] in ("single", "judge", "multi"):
                        if q["type"] == "multi":
                            # 多选必须出选项字母串：提交器按 answertype=1 发 answer{qid}=字母，
                            # 旧代码把 multi 丢给文本生成 → 回来一整句话，提交即错（用户实测）。
                            ans, conf, raw = pv.solve_multi(cfg, keys, stem, q["options"])
                        else:
                            ans, conf, raw = pv.solve_choice(cfg, keys, stem, q["options"])
                        if img_cap is not None:
                            conf = min(conf, img_cap)
                        if not ans and _is_refusal(raw):
                            res.update(answer="", status="refused", err="模型称缺题面")
                            if on_event: on_event("warn", "%s 模型拒答(缺题面)" % q["qid"])
                            break
                        res.update(answer=ans, confidence=conf, source=cfg["name"],
                                   status="ok" if ans else "noviable")
                    else:
                        # 填空/多选/简答：文本生成。风格令见 _text_prompt()
                        # （用户要求：答案简洁、语言平实、去 AI 味、像学生写的）
                        txt = pv.chat(cfg, keys,
                                      [{"role": "user", "content": _text_prompt(q)}],
                                      max_tokens=800)
                        if _is_refusal(txt):
                            # 模型拒答≠答案：标失败换下一家，全链拒答则留空待人工
                            res.update(answer="", status="refused", err="模型称缺题面")
                            if on_event: on_event("warn", "%s 模型拒答(缺题面)" % q["qid"])
                            break
                        res.update(answer=txt, confidence=min(0.6, img_cap) if img_cap else 0.6,
                                   source=cfg["name"], status="ok")
                    if res["status"] == "ok":
                        break
                except pv.ProviderError as e:
                    res["status"] = "provider_err"
                    res["err"] = str(e)[:120]
                    if on_event: on_event("warn", "%s %s: %s" % (cfg["name"], q["qid"], str(e)[:60]))
                    break  # 这家坏了换下一家
                time.sleep(0.4)
            res["tries"] += attempt
            if res["status"] == "ok":
                break
        job.results[q["qid"]] = res
        if on_event:
            on_event("q", "%s %s -> %s (%.2f @%s)" % (
                q["type"], q["qid"], res["answer"][:20] or res["status"],
                res["confidence"], res["source"]))
        time.sleep(random.uniform(rl[0], rl[1]))
    ok = all(r["status"] == "ok" for r in job.results.values())
    low = [qid for qid, r in job.results.items()
           if r["status"] == "ok" and r["confidence"] < settings.get("confidence_threshold", 0.75)]
    job.state = "done" if ok and not low else "partial"
    if on_event:
        on_event("end", "完成 %s，低置信留空 %d 题" % (job.progress(), len(low)))
    return job


def export_job(job, dirname=None):
    dirname = dirname or os.path.join(DATA, "answers")
    os.makedirs(dirname, exist_ok=True)
    ref = job.work_ref
    fn = os.path.join(dirname, "%s__%s.json" % (ref.get("courseId", "?"), ref.get("workId", "?")))
    with open(fn, "w", encoding="utf-8") as f:
        json.dump({"work_ref": ref, "state": job.state, "started": job.started,
                   "results": list(job.results.values())}, f, ensure_ascii=False, indent=1)
    return fn
