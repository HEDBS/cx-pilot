# -*- coding: utf-8 -*-
"""签到码获取（signcode）—— 只读，零提交副作用。

链路（2026-09-27 实测）：
  1. mooc1 /visit/courses            全部课程 (courseId, classId, cpi)
  2. mobilelearn activelist          每课签到活动（含 startTime/endTime 毫秒）
  3. newsign/signDetail?activePrimaryId=&type=1
       → 进行中活动返回 signCode 明文（六位数），无码回 None

判定「进行中」: startTime <= now < lateEndTime(优先) 或 endTime。
用法：
  python -m core.signcode            # 扫一遍：全部未结束活动 + 码
  python -m core.signcode --watch 20 # 每 20s 轮询，新活动/新码即打印
凭据：data/credentials.json（手机号+密码，本机明文，勿外传）
"""
import json
import os
import re
import sys
import time

from core.client import Client, DATA, ApiError, SessionExpired
from core import sign as signmod

ML = signmod.ML
# 「进行中」宽限：endTime 之后还有 lateEndTime（迟到窗口），统一用 end + 3h 判活
LATE_GRACE_MS = 3 * 3600 * 1000


def _ms(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def _safe_courses(client, tries=3):
    """课程列表偶发返回无匹配的变体页 → 空列表；重试兜底。"""
    for i in range(tries):
        courses = signmod.list_courses(client)
        if courses:
            return courses
        print("  课程列表为空，重试 %d/%d" % (i + 1, tries), flush=True)
        time.sleep(2 + i * 2)
    return []


def open_activities(client, courses=None, include_past=False):
    """全课程扫活动，返回未结束（或 include_past 全部）的活动。"""
    courses = courses or _safe_courses(client)
    now = int(time.time() * 1000)
    out = []
    for cid, cls, cpi, name in courses:
        try:
            acts = signmod.list_activities(client, cid, cls)
        except Exception as e:                                    # noqa: BLE001
            print("  [%s] activelist 失败: %s" % (name[:14], str(e)[:60]), flush=True)
            continue
        for a in acts:
            end = _ms(a.get("end"))
            if not include_past and (not end or end + LATE_GRACE_MS < now):
                continue                                          # 迟到窗口也过了的跳过
            out.append({"course": name, "courseid": cid, "clazzid": cls,
                        "active_id": a["active_id"], "name": a.get("name"),
                        "start": _ms(a.get("start")), "end": end,
                        "state": _state(a.get("start"), a.get("end"), now)})
        time.sleep(0.25)
    return out


def _state(start, end, now):
    if start and start > now:
        return "未开始"
    if end and end > now:
        return "进行中"
    return "已结束"


def fetch_signcode(client, active_id):
    """signDetail → (signCode|None, 活动名, otherId)。实测码就在这个接口明文下发。"""
    url = "%s/newsign/signDetail?activePrimaryId=%s&type=1" % (ML, active_id)
    try:
        t = client.raw_get(url, referer=ML + "/page/active/signIndex",
                           headers={"User-Agent": signmod.MOBILE_UA})
        j = json.loads(t.lstrip("@").strip())
    except Exception as e:                                        # noqa: BLE001
        return None, "", None, "ERR %s" % str(e)[:60]
    return (str(j.get("signCode") or "") or None,
            j.get("name") or "",
            j.get("otherId"),
            None)


def scan(client, verbose=True):
    """一轮完整扫描：活动 → 逐个查码。返回结果列表。"""
    results = []
    acts = open_activities(client)
    if verbose:
        print("未结束活动 %d 条（迟到窗口 3h 内；空=现在没有签到在开）" % len(acts), flush=True)
    for a in acts:
        code, aname, other, err = fetch_signcode(client, a["active_id"])
        a["sign_type"] = aname
        a["other_id"] = other
        a["sign_code"] = code
        a["error"] = err
        results.append(a)
        if verbose:
            line = "%-22s %-6s %s  %s" % (a["course"][:22], a["state"],
                                          a["name"] or aname or "", a["active_id"])
            if code:
                line += "   ★ 签到码=%s" % code
            elif err:
                line += "   (%s)" % err
            print(line, flush=True)
        time.sleep(0.2)
    return results


def watch(interval=20):
    """轮询模式：新出现的活动或新出现的码立刻打印并落盘。Ctrl+C 停。"""
    known, seen_codes = {}, set()
    print("轮询模式，每 %ds 一轮（Ctrl+C 停止）" % interval, flush=True)
    logf = os.path.join(DATA, "signcode_watch.log")
    while True:
        try:
            c = Client()
            c.ensure_login()
            for r in scan(c, verbose=False):
                key = r["active_id"]
                if key not in known:
                    known[key] = r
                    print("[新活动] %s | %s | %s | %s" % (r["state"], r["course"][:16],
                                                          r["sign_type"] or r["name"], key), flush=True)
                if r["sign_code"] and key not in seen_codes:
                    seen_codes.add(key)
                    msg = ("[★签到码] %s | %s | %s → %s" %
                           (r["course"][:16], r["sign_type"], key, r["sign_code"]))
                    print(msg, flush=True)
                    with open(logf, "a", encoding="utf-8") as f:
                        f.write(time.strftime("%F %T ") + msg + "\n")
        except KeyboardInterrupt:
            raise
        except Exception as e:                                    # noqa: BLE001
            print("轮询异常（下轮重试）:", str(e)[:100], flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        i = sys.argv.index("--watch")
        iv = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) and sys.argv[i + 1].isdigit() else 20
        watch(iv)
    else:
        c = Client()
        c.ensure_login()
        rows = scan(c)
        codes = [r for r in rows if r["sign_code"]]
        print("\n===== 结果：%d 条未结束活动，%d 条有码 =====" % (len(rows), len(codes)))
        for r in codes:
            print("  %s | %s → %s" % (r["course"][:20], r["sign_type"], r["sign_code"]))
        os.makedirs(DATA, exist_ok=True)
        p = os.path.join(DATA, "signcode_scan.json")
        json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("落盘 ->", p)
