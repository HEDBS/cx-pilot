# -*- coding: utf-8 -*-
"""P5 提交层：把 job.results 注入 dowork 页 hidden 字段 -> POST addStudentWorkNewWeb。
纪律：默认 dry_run；真提交需要调用方显式确认。限速由上层控制。"""
import json
import re
import time
import urllib.parse
import urllib.request

from core.client import Client, UA, strip_html
from core import providers as pv


class SubmitError(Exception):
    pass


def _as_editor_html(s):
    """UEditor sync() 后 textarea 里的真实形态：纯文本按行包 <p>。"""
    s = str(s) if s is not None else ""
    return "".join("<p>%s</p>" % ln for ln in s.split("\n") if ln.strip()) or ""


def build_form(qs, results, ctx, work_ref):
    hidden = dict(ctx.get("hidden") or {})
    form = {
        "courseId": work_ref["courseId"],
        "classId": work_ref["classId"],
        "knowledgeid": hidden.get("knowledgeid", "0"),
        "cpi": work_ref["cpi"],
        "workRelationId": work_ref["workId"],
        "workAnswerId": work_ref["answerId"],
        "jobid": hidden.get("jobid", ""),
        "standardEnc": ctx.get("standardEnc", ""),
        "enc_work": hidden.get("enc_work", ""),
        "totalQuestionNum": hidden.get("totalQuestionNum", ""),
        "pyFlag": hidden.get("pyFlag", "3"),
        # 真协议（2026-09-28 实测定案）：answerwqbid 页面 hidden 为空串，但服务端
        # 必校验它=qid 逗号串（尾逗号），发空被拒「无效的参数：code-1」。
        "answerwqbid": hidden.get("answerwqbid") or (",".join(q["qid"] for q in qs) + ","),
        "mooc2": "1",
        "uploadEnc": hidden.get("uploadEnc", hidden.get("enc", "")),
        "enc": hidden.get("enc", ""),
        "matchEnc": hidden.get("matchEnc", ""),
        "workTimesEnc": hidden.get("workTimesEnc", ""),
        "randomOptions": hidden.get("randomOptions", "false"),
        "questionIds": ",".join(q["qid"] for q in qs) + ",",
    }
    for q in qs:
        r = results.get(q["qid"]) or {}
        ans = r.get("answer", "")
        if q["type"] in ("single", "multi"):
            # 选项字母按页面字母序拼接；多选=无分隔
            form["answertype%s" % q["qid"]] = "0" if q["type"] == "single" else "1"
            form["answer%s" % q["qid"]] = ans
        elif q["type"] == "blank":
            # 真协议（2026-09-28 实测定案）：服务端拒收纯文本 answer{qid}
            # （"无效的参数：code-1"）。页面 serialize 提交的是每空一个
            # answerEditor{qid}{n}，n=1..tiankongsize，UEditor 内容。
            form["answertype%s" % q["qid"]] = "2"
            blanks = list(ans) if isinstance(ans, (list, tuple)) else [ans]
            form["tiankongsize%s" % q["qid"]] = str(len(blanks))
            for n, one in enumerate(blanks, 1):
                form["answerEditor%s%d" % (q["qid"], n)] = _as_editor_html(one)
        else:
            form["answertype%s" % q["qid"]] = "4"
            form["answer%s" % q["qid"]] = ans or ""
    return form


def _bump_version(url):
    m = re.search(r"&version=(\d+)", url)
    if m:
        return re.sub(r"&version=\d+", "&version=%d" % (int(m.group(1)) + 1), url), \
               {"ua": "pc", "formType": "1", "saveStatus": "1"}
    return url + "&version=1", {"ua": "pc", "formType": "1", "saveStatus": "1"}


def submit(client: Client, qs, results, ctx, work_ref, confirm=False, referer=None):
    """返回 {status, msg, raw}。confirm=False 时只构造请求不发送。"""
    action = ctx.get("form_action")
    if not action:
        raise SubmitError("form_action 缺失，dowork 页结构变了")
    url, extra_q = _bump_version(action)
    q = urllib.parse.urlencode(extra_q)
    url = url + ("&" if "?" in url else "?") + q
    form = build_form(qs, results, ctx, work_ref)
    if not confirm:
        preview = {k: (v[:60] + "...") if isinstance(v, str) and len(v) > 60 else v
                   for k, v in list(form.items())[:20]}
        return {"status": "dry_run", "msg": "未发送（需要 confirm=True）", "url": url, "form_preview": preview}
    full = "https://mooc1.chaoxing.com" + url
    referer = referer or ("https://mooc1.chaoxing.com/mooc-ans/mooc2/work/dowork?courseId=%s&classId=%s"
                          "&cpi=%s&workId=%s&answerId=%s" % (work_ref["courseId"], work_ref["classId"],
                                                             work_ref["cpi"], work_ref["workId"], work_ref["answerId"]))
    t = client.raw_post(full, form, referer=referer,
                        headers={"X-Requested-With": "XMLHttpRequest"})
    try:
        j = json.loads(t)
    except Exception:
        raise SubmitError("非JSON响应(len=%d): %s" % (len(t), strip_html(t)[:100]))
    if not j.get("status"):
        raise SubmitError("服务端拒绝: " + str(j.get("msg"))[:150])
    return {"status": "ok", "msg": str(j)[:200], "raw": j}
