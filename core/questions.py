# -*- coding: utf-8 -*-
"""P2 题目层：dowork 页解析为题目池 + 作业直达链。
链: stat2/getWorkStuUrl 或 getAllWork 直链 -> mooc2/work/prompt -> dowork 页 -> parse。
"""
import json
import re

from core.client import Client, strip_html, SessionExpired

QTYPE = {"0": "single", "1": "multi", "2": "blank", "3": "judge",
         "4": "subjective", "5": "subjective", "6": "subjective",
         "7": "subjective", "8": "subjective", "9": "blank", "10": "blank",
         "11": "blank", "13": "blank", "14": "blank", "18": "subjective",
         "26": "subjective"}


def parse_work_page(html):
    """dowork 页 -> [{qid,type,stem,options,image_flag,blank_count}]"""
    out = []
    for m in re.finditer(r'<div class="[^"]*"[^>]*id="question(\d+)"[^>]*>', html):
        qid = m.group(1)
        head = html[max(0, m.start() - 400):m.start() + 60]
        tn = re.search(r'typeName="([^"]*)"', m.group(0))
        tname = tn.group(1) if tn else ""
        i = m.end()
        nxt = re.search(r'<div class="[^"]*"[^>]*id="question\d+"', html[i:])
        ch = html[i: i + (nxt.start() if nxt else 30000)]
        typecode = ""
        tc = re.search(r'id="answertype%s"[^>]*value="(\d+)"' % qid, ch)
        if tc:
            typecode = tc.group(1)
        qtype = QTYPE.get(typecode, "subjective" if "单选" not in tname and "多选" not in tname else "single")
        stem_m = re.search(r'<h3[^>]*class="mark_name[^"]*"[^>]*>(.*?)</h3>', ch, re.S)
        stem = ""
        if stem_m:
            stem = re.sub(r'^\d+\.\s*', '', stem_m.group(1))
            stem = re.sub(r'<span[^>]*>\((?:单选题|多选题|填空题|判断题|主观题)\)</span>', '', stem)
        has_img = "<img" in (stem_m.group(1) if stem_m else "")
        # B6 识图链：题图 URL 抠出来（ananas CDN），供 OCR/vision_api 下载
        img_urls = re.findall(r'<img[^>]*src="([^"]+)"',
                              stem_m.group(1) if stem_m else "")
        img_urls = [u for u in img_urls if "ananas" in u or u.startswith("//")]
        stem_txt = strip_html(stem)[:2000]
        q = {"qid": qid, "type": qtype, "type_name": tname, "stem": stem_txt,
             "image_flag": has_img, "img_urls": img_urls, "options": {}}
        if qtype in ("single", "multi"):
            for om in re.finditer(
                    r'<span[^>]*data="([A-G])"[^>]*class="choice%s[^"]*"[^>]*>[A-G]</span>\s*'
                    r'<div[^>]*class="fl answer_p"[^>]*>(.*?)</div>' % qid, ch, re.S):
                q["options"][om.group(1)] = strip_html(om.group(2))[:500]
            if q["image_flag"] is False and q["options"] == {}:
                # 兜底：选项可能整块含图
                q["image_flag"] = bool(re.search(r'choice%s' % qid, ch))
        elif qtype == "judge":
            # 判断题（真卷实测 2026-09-29）：题面是 <span data="true">A</span><p>对</p>
            # 与 <span data="false">B</span><p>错</p>；提交值是 data 的原值（true/false），
            # 不是字母 A/B（页面 addChoice 直接取 data 写进 hidden answer{qid}）。
            # 旧正则只认 data="[A-G]"，判断题被整类漏掉 → options 空 → 恒 noviable。
            jmap = {}
            for om in re.finditer(
                    r'<span[^>]*data="(true|false)"[^>]*class="choice%s[^"]*"[^>]*>[A-G]</span>\s*'
                    r'<div[^>]*class="fl answer_p"[^>]*>(.*?)</div>' % qid, ch, re.S):
                letter = "A" if om.group(1) == "true" else "B"
                label = strip_html(om.group(2))[:20]
                q["options"][letter] = label or ("对" if letter == "A" else "错")
                jmap[letter] = om.group(1)
            if jmap:
                q["judge_map"] = jmap   # 字母 -> true/false，提交层据此还原协议值
        if qtype == "blank":
            # 真卷实测(2026-09-28)：空数权威来源是 hidden tiankongsize{qid}；
            # 下划线在题图里，stem 文本数不到 → 旧逻辑恒 1 是错的
            ts = re.search(r'name="tiankongsize%s"[^>]*value="(\d+)"' % qid, ch)
            q["blank_count"] = int(ts.group(1)) if ts else max(1, len(re.findall(r'_{3,}', stem_txt)))
        out.append(q)
    return out


def fetch_work_questions(client: Client, courseid, clazzid, cpi, workid, answerid="0",
                         enc="", standard_enc=""):
    """领卷 -> 解析题目。返回 (questions, ctx)；ctx 含提交要用的 token/enc。"""
    page = None
    if not enc:
        # 走 stat2 直达链拿新鲜 enc/answerId。
        # 2026-09-28 e2e 第4环实锤：该接口行为变了——对已排期作业 302 直落 dowork 页
        # （urllib 跟随重定向后拿到的是整张 HTML 卷子而非 JSON），未排期作业也返回 HTML。
        # 用单次 raw_get 兼容两种契约：JSON 照旧抠 enc/answerId；已是卷子页就直接用它，
        # 省掉后面的 prompt 一跳（get_json 走 JSON 解析会重试 3 次纯浪费，故不再用）。
        gurl = ("https://stat2-ans.chaoxing.com/stat2/learning/plan/"
                "getWorkStuUrl?courseid=%s&clazzid=%s&cpi=%s&workId=%s"
                % (courseid, clazzid, cpi, workid))
        body = client.raw_get(gurl)
        if body[:1] in "@{":
            try:
                j = json.loads(body.lstrip("@"))
            except Exception:
                j = {}
            url = j.get("data") or j.get("url") or ""
            mm = re.search(r"answerId=(\d+).*?enc=([0-9a-f]{32})", url)
            if mm:
                answerid, enc = mm.group(1), mm.group(2)
        elif 'addStudentWorkNewWeb' in body or 'id="question' in body:
            page = body   # 重定向已到卷子页：直接消费，enc 不再需要
            ma = re.search(r'name="workAnswerId"[^>]*value="(\d+)"', body)
            if ma:
                answerid = ma.group(1)
    referer = "https://mooc1.chaoxing.com/"
    if not standard_enc:
        ie = client.get_json("https://mooc1.chaoxing.com/mooc-ans/work/isExpire?courseId=%s"
                             "&classId=%s&cpi=%s&workRelationId=%s&answerId=%s&workId=%s"
                             % (courseid, clazzid, cpi, workid, answerid, workid),
                             referer=referer)
        standard_enc = ie.get("data", {}).get("standardEnc", "") if isinstance(ie.get("data"), dict) else ie.get("standardEnc", "")
    # 领卷（直达链已给卷子页时不再走 prompt）
    if page is None:
        prompt_url = ("https://mooc1.chaoxing.com/mooc-ans/mooc2/work/prompt?courseId=%s&classId=%s"
                      "&cpi=%s&workId=%s&answerId=%s&enc=%s" % (courseid, clazzid, cpi, workid, answerid, enc))
        page = client.raw_get(prompt_url, referer=referer)
    qs = parse_work_page(page)
    # 提交上下文：form action 里的 token / totalQuestionNum + hidden 群
    fa = re.search(r'action="(/mooc-ans/work/addStudentWorkNewWeb\?[^"]+)"', page)
    action = fa.group(1).replace("&amp;", "&") if fa else ""
    hidden = {}
    for hm in re.finditer(r'<input[^>]*type="hidden"[^>]*name="(\w+)"[^>]*value="([^"]*)"', page):
        hidden[hm.group(1)] = hm.group(2)
    limit = re.search(r'id="limitWorkSubmitTimes"[^>]*value="(\d+)"', page)
    ctx = {"form_action": action, "hidden": hidden, "standardEnc": standard_enc,
           "limit_submit": int(limit.group(1)) if limit else 100, "page_len": len(page),
           "answerId": answerid}
    return qs, ctx
