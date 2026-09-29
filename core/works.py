# -*- coding: utf-8 -*-
"""P1: 逐课 getAllWork 兜底刷新（实时状态）+ 全部作业聚合。"""
import datetime
import json
import os
import random
import re
import time
from dataclasses import dataclass, asdict

from core.client import Client, BASE, DATA, strip_html, RiskControl, RISK_COOLDOWN_S

MOOC = "https://mooc1.chaoxing.com"

# 列表页标题剔除词表（v0.3.1 修订）：随堂练习不再剔除——很多课的"随堂练习"
# 本质就是作业；能不能做交给审核层（audit 领卷验真）判定，不在入口误杀。
TITLE_EXCLUDES = ("分组任务", "PBL")


def _excluded_title(title):
    return any(k in (title or "") for k in TITLE_EXCLUDES)


@dataclass
class Work:
    course: str
    courseid: str
    clazzid: str
    cpi: str
    work_id: str
    answer_id: str
    title: str
    status: str            # 待做/已完成/已过期/待批阅...
    start: str
    deadline: str
    deadline_ts: int       # 解析失败为 0
    work_enc: str          # 作业场景 enc（原样抠，不换算）
    yipiyue_url: str = ""  # 已批查看直链（若有）


def _parse_dt(s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.datetime.strptime(s, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return 0


def _course_triplets(client):
    """课程列表 → (courseId, classId, cpi, name) 四元组。"""
    t = client.raw_get(MOOC + "/visit/courses", referer=MOOC + "/")
    pat = re.compile(r"<a class=\"courseName\"\s+href='/visit/stucoursemiddle\?courseid=(\d+)&clazzid=(\d+)&vc=\d+&cpi=(\d+)'[^>]*title=\"([^\"]*)\"")
    seen, out = set(), []
    for cid, cls, cpi, name in pat.findall(t):
        if cid in seen:
            continue
        seen.add(cid)
        out.append((cid, cls, cpi, name.replace("&nbsp;", " ")))
    return out


def _works_of_course(client, cid, cls, cpi, name):
    portal = client.raw_get("%s/visit/stucoursemiddle?courseid=%s&clazzid=%s&vc=1&cpi=%s" % (MOOC, cid, cls, cpi),
                            referer=MOOC + "/visit/courses")
    m = re.search(r"(/work/getAllWork\?[^'\"]+)", portal)
    if not m:
        return []
    gpath = m.group(1).replace("&amp;", "&") + "&start=0&size=50"
    wl = client.raw_get(MOOC + "/mooc-ans" + gpath)
    works = []
    for blk in re.split(r'<div class="titTxt"', wl)[1:]:
        a = re.search(r'class="inspectTask"[^>]*?data="(\d+)"[^>]*?data2="(\d+)"[^>]*?data3="(\d+)"[^>]*?title="([^"]+)"', blk, re.S)
        b = None
        if not a:
            b = re.search(r'selectWorkQuestionYiPiYue\?[^"]*workId=(\d+)&workAnswerId=(\d+)[^"]*"[^>]*title="([^"]+)"', blk)
        if not a and not b:
            continue
        if a:
            wid, waid, title = a.group(1), a.group(2), a.group(4)
        else:
            wid, waid, title = b.group(1), b.group(2), b.group(3)
        if _excluded_title(title):       # B4h：非作业条目不入库（随堂练习保留，由审核验真）
            continue
        st = re.search(r"<strong>\s*([^<]+?)\s*</strong>", blk)
        end = re.search(r"截止时间：</span>([^<]+)", blk)
        start = re.search(r"开始时间：</span>([^<]+)", blk)
        we = re.search(r"enc=([0-9a-f]{32})", blk)
        yy = re.search(r'href="(/mooc-ans/work/selectWorkQuestionYiPiYue[^"]*workId=%s[^"]*)"' % wid, blk)
        dl = end.group(1).strip() if end else ""
        works.append(Work(course=name, courseid=cid, clazzid=cls, cpi=cpi,
                          work_id=wid, answer_id=waid, title=title,
                          status=st.group(1).strip() if st else "",
                          start=start.group(1).strip() if start else "",
                          deadline=dl, deadline_ts=_parse_dt(dl),
                          work_enc=we.group(1) if we else "",
                          yipiyue_url=yy.group(1) if yy else ""))
    return works


def refresh_all(client, max_courses=None, delay=(0.6, 1.4), progress=None, on_rows=None):
    """全量遍历。每课 2 个请求(门户+列表)。progress(i,n) 可选回调。

    on_rows(works_of_one_course) 可选：每课解析完立即回调一次，供上层增量入库
    （用户实测反馈：整轮扫完才一次性冒出全部卡片，等着难受）。
    与 progress 同纪律：回调异常不得影响扫描，但 CancelledError 属 BaseException，
    会穿透这里的 except Exception —— 这正是 /cancel 真取消的机制，必须保持。

    风控自愈（真环境实测 2026-09-29）：课程列表页被超星罚站时先丢弃过期 cookie 全新
    登录再试一次；仍被拦就**抛出** RiskControl——绝不静默返回 0 条（用户会误判「没作业」）。
    """
    try:
        trips = _course_triplets(client)
    except RiskControl:
        # 级联：先磁盘重载（免打登录接口），不行再真重登；仍被拦则抛出
        client.reload_cookies()
        try:
            trips = _course_triplets(client)
        except RiskControl:
            time.sleep(RISK_COOLDOWN_S)   # 风控看 IP 热度，重登前先冷却（实测有效）
            if not client.recover_session():
                raise
            trips = _course_triplets(client)   # 仍被拦则原样抛出，由上层显式报错
    if max_courses:
        trips = trips[:max_courses]
    all_w = []
    risk_skipped = []
    for i, (cid, cls, cpi, name) in enumerate(trips):
        if progress:
            try:
                progress(i, len(trips))
            except Exception:
                pass
        try:
            ws = _works_of_course(client, cid, cls, cpi, name)
            all_w.extend(ws)
            print("[%d/%d] %s -> %d 条" % (i + 1, len(trips), name[:18], len(ws)))
            if on_rows:
                try:
                    on_rows(ws)
                except Exception:
                    pass
        except RiskControl:
            # 单课中途被罚站：只做**免费**的磁盘重载再试一次。
            # 不要在这里逐课重登——那会连发几十次登录请求被超星限流，
            # 表现就是进度条冻住（实测踩过）。仍被拦→跳过这一课并记账，
            # 绝不整轮中止，也绝不静默（末尾汇总）。
            client.reload_cookies()
            try:
                ws = _works_of_course(client, cid, cls, cpi, name)
                all_w.extend(ws)
                print("[%d/%d] %s -> %d 条（重载 cookie 后重试）"
                      % (i + 1, len(trips), name[:18], len(ws)))
                if on_rows:          # 重试成功的课也要走增量回调，否则它的卡片得等收尾才冒出来
                    try:
                        on_rows(ws)
                    except Exception:
                        pass
            except RiskControl as e2:
                risk_skipped.append(name)
                print("[%d/%d] %s 风控跳过：%s" % (i + 1, len(trips), name[:18], str(e2)[:50]))
        except Exception as e:
            print("[%d/%d] %s ERR %s" % (i + 1, len(trips), name[:18], str(e)[:60]))
        time.sleep(delay[0] + random.random() * (delay[1] - delay[0]))
    if risk_skipped:
        print("⚠ 有 %d 门课被风控跳过（%s）——等 1-2 分钟冷后再扫一次即可补齐，不是没作业"
              % (len(risk_skipped), "、".join(risk_skipped[:4])))
    if progress:
        try:
            progress(len(trips), len(trips))
        except Exception:
            pass
    return all_w


def save_works(works, path=None):
    os.makedirs(DATA, exist_ok=True)
    p = path or os.path.join(DATA, "works.json")
    data = [asdict(w) for w in works]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return p


if __name__ == "__main__":
    c = Client()
    c.ensure_login()
    ws = refresh_all(c)
    p = save_works(ws)
    from collections import Counter
    print("总计", len(ws), dict(Counter(w.status for w in ws)), "->", p)
