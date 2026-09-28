# -*- coding: utf-8 -*-
"""P1: 逐课 getAllWork 兜底刷新（实时状态）+ 全部作业聚合。"""
import datetime
import json
import os
import random
import re
import time
from dataclasses import dataclass, asdict

from core.client import Client, BASE, DATA, strip_html

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


def refresh_all(client, max_courses=None, delay=(0.6, 1.4), progress=None):
    """全量遍历。每课 2 个请求(门户+列表)。progress(i,n) 可选回调。"""
    trips = _course_triplets(client)
    if max_courses:
        trips = trips[:max_courses]
    all_w = []
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
        except Exception as e:
            print("[%d/%d] %s ERR %s" % (i + 1, len(trips), name[:18], str(e)[:60]))
        time.sleep(delay[0] + random.random() * (delay[1] - delay[0]))
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
