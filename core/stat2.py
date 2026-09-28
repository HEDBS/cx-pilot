# -*- coding: utf-8 -*-
"""P1: 官方学习规划聚合接口（stat2）——主查询入口 + 三种排序。
数据每 4h 官方刷新；要实时状态用 works.refresh_all()。"""
import datetime
import json
import os
import re
from dataclasses import dataclass, field, asdict

from core.client import Client, BASE, DATA

STAT2 = "https://stat2-ans.chaoxing.com"
PLAN = STAT2 + "/stat2/learning/plan"


@dataclass
class Task:
    course: str
    courseid: str
    clazzid: str
    cpi: str
    task_id: str          # = workId
    name: str
    event_type: str       # work / exam / topic ...
    end_time: int         # epoch ms
    end_date: str         # "09-27 23:59"
    remain_str: str       # "剩余1天0小时"
    question_num: int
    url: str              # getWorkStuUrl 直达链
    status: str = "todo"  # 聚合接口只给临期任务，默认 todo


def _ids_from_url(url):
    m = re.search(r"courseid=(\d+).*?clazzid=(\d+).*?cpi=(\d+)", url or "")
    if m:
        return m.group(1), m.group(2), m.group(3)
    return "", "", ""


def fetch_near_tasks(client: Client):
    """1 个请求拿全部临期任务 + 重点课程进度。"""
    j = client.get_json(PLAN + "/recommended-course-list",
                        referer=STAT2 + "/stat2-vue/studyPlanAssistant")
    if j.get("code") != 0:
        raise RuntimeError("stat2 code=%s msg=%s" % (j.get("code"), j.get("msg")))
    d = j["data"]
    tasks = []
    for t in d.get("allNearTasks") or []:
        cid, cls, cpi = _ids_from_url(t.get("url", ""))
        tasks.append(Task(course=t.get("courseName", ""), courseid=cid, clazzid=cls, cpi=cpi,
                          task_id=str(t.get("id", "")), name=t.get("name", ""),
                          event_type=t.get("eventType", ""), end_time=int(t.get("endTime", 0)),
                          end_date=t.get("endDate", ""), remain_str=t.get("remainTimeStr", ""),
                          question_num=int(t.get("questionNum", 0) or 0), url=t.get("url", "")))
    courses = []
    for c in (d.get("courseList") or []) + (d.get("otherCourseList") or []):
        courses.append({
            "courseName": c.get("courseName"), "clazzId": c.get("clazzId"),
            "className": c.get("className"), "progress": c.get("learningProgressPer"),
            "nearTaskNum": c.get("nearTaskNum"), "weakNum": c.get("weakKnowledgePointsNum"),
        })
    return {"generated": d.get("cacheGenerateTime", ""), "tasks": tasks, "courses": courses}


def sort_by_time(tasks):
    return sorted(tasks, key=lambda t: t.end_time)


def sort_by_course(tasks):
    return sorted(tasks, key=lambda t: (t.course, t.end_time))


def sort_by_course_excluding(tasks, exclude_courses):
    """科目横向排开后，去掉指定科目，剩下的按时间纵向排（模式3）。"""
    ex = set(exclude_courses)
    left = [t for t in tasks if t.course not in ex]
    return sort_by_time(left)


def remain_hours(task, now=None):
    now = now or datetime.datetime.now()
    et = datetime.datetime.fromtimestamp(task.end_time / 1000)
    return round((et - now).total_seconds() / 3600, 1)


def save_snapshot(snap):
    os.makedirs(DATA, exist_ok=True)
    p = os.path.join(DATA, "tasks.json")
    data = dict(snap)
    data["tasks"] = [asdict(t) for t in snap["tasks"]]
    data["fetched_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return p


if __name__ == "__main__":
    c = Client()
    c.ensure_login()
    snap = fetch_near_tasks(c)
    print("官方缓存时间:", snap["generated"], "| 临期任务:", len(snap["tasks"]))
    print("\n—— 模式1 仅时间排序 ——")
    for t in sort_by_time(snap["tasks"]):
        print("  %s | %s | %s | %d题 | %s" % (t.end_date, remain_hours(t), t.course[:14], t.question_num, t.name[:26]))
    print("\n—— 模式2 科目排序 ——")
    for t in sort_by_course(snap["tasks"]):
        print("  %s | %s | %s" % (t.course[:16], t.name[:26], t.end_date))
    if snap["tasks"]:
        ex = [snap["tasks"][0].course]
        print("\n—— 模式3 排除%s后按时间 —— " % ex[0])
        for t in sort_by_course_excluding(snap["tasks"], ex):
            print("  %s | %s | %s" % (t.course[:16], t.name[:24], t.remain_str))
    print("快照 ->", save_snapshot(snap))
