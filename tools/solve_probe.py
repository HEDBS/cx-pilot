#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""解题层回归门（**离线**，不打网络；用打桩 chat 走真实 solver 代码路径）。

守的是两条用户实测 bug：
 1) 「扫描出来是多选，解题器却当简答答」——multi 被丢进文本生成，回来一整句话；
    而提交器按 answertype=1 发 `answer{qid}`=字母串 → 提交必然判错。
 2) 简答答案一股 AI 味（首先/其次/综上所述 + 加粗 + 分点 + 铺陈）——用户要求
    「简洁、语言平实、去 AI 味、像学生写出来的」，风格令固化在 _text_prompt 里。

跑法：python tools/solve_probe.py     （README/回归门清单里按需登记）
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import providers as pv                                    # noqa: E402
from core import questions as qs_mod                                # noqa: E402
from core import solver                                             # noqa: E402
from core.providers import _extract_letters                         # noqa: E402
from core.solver import _text_prompt                                # noqa: E402

FAIL = []
N = [0]


def ck(name, cond, got="-", want="-"):
    N[0] += 1
    if cond:
        print("  ✓ %s" % name)
    else:
        FAIL.append(name)
        print("  ✗ %s   实测=%r 期望=%r" % (name, got, want))


# ---------- A. 多选字母提取器（模型脏输出）----------
print("A. 多选字母提取器")
V = {"A": "封装", "B": "继承", "C": "多态", "D": "编译"}
CASES = [("ABD", "ABD"), ("ABC\n", "ABC"), ("最后一行：ABC", "ABC"), ("答案：ABC", "ABC"),
         ("正确答案是 A、B、C", "ABC"), ("正确答案：ABD。", "ABD"), ("A C B", "ABC"),
         ("A,B,C", "ABC"), ("选 ABC", "ABC"), ("我认为应该是ABC三个选项", "ABC"),
         ("分析：A 对，B 对，C 对，D 不对。\n答案：ABC", "ABC"),
         ("ABC\nD（编译不是特征）", "ABC"), ("没有正确答案", ""), ("ABCD", "ABCD"),
         ("The FACE of it", ""), ("答案：D", "D")]
for raw, want in CASES:
    got = _extract_letters(raw, V.keys())
    ck("提取 %r" % raw[:26], got == want, got, want)

# ---------- B. 路由：multi 必须走多选模板并出字母串 ----------
print("\nB. 题型路由（打桩 chat，走真实 solve_job）")
SENT = []


def _fake_chat(cfg, keys, messages, **kw):
    SENT.append(messages[0]["content"])
    return "分析：A 封装、B 继承、C 多态都对，D 编译不是。\n答案：ABC"


DEF = {"providers": [{"kind": "openai", "name": "探针后端", "base_url": "http://probe.invalid",
                      "model": "m", "enabled": True, "key_ref": "probe"}]}
KEYS = {"probe": "x"}
_orig_chat = pv.chat
pv.chat = _fake_chat
try:
    job = solver.Job({"course": "探针", "title": "探针"},
                     [{"qid": "q1", "type": "multi", "stem": "以下哪些属于面向对象的特征？",
                       "options": {"A": "封装", "B": "继承", "C": "多态", "D": "编译"}}])
    solver.solve_job(job, settings=DEF, keys=KEYS)
    r = job.results.get("q1") or {}
    ck("multi 的答案是选项字母串（不是整句话）",
       r.get("answer") == "ABC" and str(r.get("answer", "")).isalpha(), r.get("answer"), "ABC")
    ck("multi 走的提示词是多选模板（不是简答模板）",
       any("多选题" in p for p in SENT) and not any("简答题" in p for p in SENT),
       [p[:24] for p in SENT], "含「多选题」且不含「简答题」")

    SENT.clear()
    j2 = solver.Job({"course": "探针", "title": "探针"},
                    [{"qid": "q2", "type": "subjective", "stem": "简述进程和线程的区别。"}])
    pv.chat = lambda cfg, keys, messages, **kw: (
        SENT.append(messages[0]["content"]) or "进程有自己的内存空间，线程共享进程内存。")
    solver.solve_job(j2, settings=DEF, keys=KEYS)
    p = SENT[0] if SENT else ""
    ck("简答的提示词带风格令（简洁/平实/去 AI 味/像学生写的）",
       all(w in p for w in ("简洁", "平实", "不要任何 markdown", "像学生")), p[:40], "四项都在")
    ck("简答提示词不复用旧串「请给出答案。」", "请给出答案" not in p, p[-30:], "不含")
    ck("简答提示词明确禁用套话（首先/综上所述等）",
       "首先" in p and "综上所述" in p, "见规则③", "规则③在")
finally:
    pv.chat = _orig_chat

# ---------- C. 填空模板 ----------
print("\nC. 填空模板")
pb = _text_prompt({"type": "blank", "blank_count": 3, "stem": "（ ）是线性结构。"})
ck("填空提示词要求逐空成行、不成句", "每行一个" in pb and "3 空" in pb, pb[:36], "含「每行一个」+「3 空」")

# ---------- D. 题型兜底：抓不到 answertype 也不能把多选判成单选 ----------
print("\nD. 题型兜底（页面缺 answertype 输入时）")


def _page(tname, with_typecode):
    tc = '<input type="hidden" id="answertype901" value="1"/>' if with_typecode else ""
    return ('<div class="padBom50 questionLi" typeName="%s" id="question901">'
            '<h3 class="mark_name">1. <span>(%s)</span>以下哪些正确？</h3>%s'
            '<span data="A" class="choice901 fl">A</span>'
            '<span data="B" class="choice901 fl">B</span></div>'
            '<form action="/mooc-ans/work/addStudentWorkNewWeb?x=1">'
            '<input type="hidden" name="enc" value="e"/></form>') % (tname, tname, tc)


got_m = qs_mod.parse_work_page(_page("多选题", False))
ck("多选页缺 answertype → 仍判为 multi（旧代码会判成 single，只出一个字母）",
   bool(got_m) and got_m[0]["type"] == "multi",
   got_m[0]["type"] if got_m else "无题", "multi")
got_s = qs_mod.parse_work_page(_page("单选题", False))
ck("单选页缺 answertype → single", bool(got_s) and got_s[0]["type"] == "single",
   got_s[0]["type"] if got_s else "无题", "single")
got_t = qs_mod.parse_work_page(_page("多选题", True))
ck("有 answertype=1 → multi（正常路径不受影响）",
   bool(got_t) and got_t[0]["type"] == "multi",
   got_t[0]["type"] if got_t else "无题", "multi")

print("\nSOLVE: %d/%d %s" % (N[0] - len(FAIL), N[0], "PASS" if not FAIL else "FAIL"))
if FAIL:
    for f in FAIL:
        print("   FAIL:", f)
sys.exit(1 if FAIL else 0)
