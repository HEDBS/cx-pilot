# -*- coding: utf-8 -*-
"""TASK-A1 作业审核：扫描后「验卷」——只 GET 领卷判断任务是否可作答，附首题预览。

三态语义（docs/TASK-A1.md 定案）：
- 可作答：领卷成功且题数>0            → solvable=True,  qreal>0, preview 前 2 题题干各截 24 字
- 不可作答-非作业：stat2 etype != work → solvable=False, reason="非作业"（零请求判定）
- 不可作答-读不到：work 但领卷失败/0题 → solvable=False, reason="读不到题"/"无题"

红线：
- 本模块【只 GET】——领卷唯一入口 questions.fetch_work_questions（stat2 直达链→prompt 页，
  全链 raw_get/get_json），绝不 POST 保存/提交；submitter 协议零接触。
- audit_task 永不抛出：任何异常都归一化为「读不到」分类返回，审核线程不因单条炸掉。
- 图题只标「[图]」占位，不在审核阶段调视觉 API（省配额；答题时才读）。

缓存：audit_cache.json 落在产品数据目录（%APPDATA%\\cx-pilot，与 cookies/凭据同盘不同文件），
key = "%s:%s" % (courseId, workId)，值含结果+时间戳，TTL 4 小时（stat2 官方缓存同节奏）；
命中零请求。瞬时网络错不缓存（避免把一次抖动灰锁 4h），门槛/403 类判定缓存。
"""
import json
import os
import threading
import time

from core import client as _client   # 运行时取 _client.DATA（冒烟 monkeypatch 测试目录用）
from core import questions

TTL = 4 * 3600                        # 缓存有效期：4h，官方数据刷新节奏
PREVIEW_N = 2                         # 预览前 2 题
PREVIEW_CLIP = 24                     # 每题题干截 24 字

_lock = threading.Lock()


def cache_path():
    return os.path.join(_client.DATA, "audit_cache.json")


def cache_key(task):
    return "%s:%s" % (task.get("courseId", ""), task.get("workId", ""))


def _load_cache():
    try:
        with open(cache_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}          # 缺文件/坏 JSON 一律当空缓存，审核退化为逐条真领卷


def _cache_get(key):
    with _lock:
        ent = _load_cache().get(key)
    if not isinstance(ent, dict) or not isinstance(ent.get("result"), dict):
        return None
    try:
        fresh = (time.time() - float(ent.get("ts", 0))) <= TTL
    except (TypeError, ValueError):
        fresh = False
    return dict(ent["result"]) if fresh else None


def _cache_put(key, res):
    with _lock:
        d = _load_cache()
        now = time.time()
        d[key] = {"result": res, "ts": now}
        d = {k: v for k, v in d.items()                 # 顺手清过期，防无界增长
             if isinstance(v, dict) and now - float(v.get("ts", 0) or 0) <= TTL}
        try:
            os.makedirs(_client.DATA, exist_ok=True)
            tmp = cache_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, cache_path())
        except Exception:
            pass           # 缓存写坏不影响本进程结果（下次刷新自然重建）


def _preview_of(qs):
    """前 2 题预览：图题只标 [图]（不调视觉 API），文题干截 24 字。"""
    parts = []
    for q in qs[:PREVIEW_N]:
        if q.get("image_flag"):
            parts.append("[图]")
        else:
            stem = (q.get("stem") or "").strip()
            if stem:
                parts.append(stem[:PREVIEW_CLIP])
    return " ▸ ".join(parts)


def audit_task(client, task, force=False):
    """审核一条任务 → {audited, solvable, reason, qreal, preview, from_cache}。永不抛出。"""
    key = cache_key(task)
    if not force:
        hit = _cache_get(key)
        if hit is not None:
            hit["from_cache"] = True
            return hit
    cacheable = True
    try:
        etype = (task.get("etype") or "work").strip().lower()
        if etype != "work":
            # 签到/续签合同/阅读类……非作业连领卷都不用发（etype 本地判定，零请求）
            res = {"audited": True, "solvable": False, "reason": "非作业",
                   "qreal": 0, "preview": ""}
        elif not (task.get("classId") or ""):
            # v0.3.1：活动/实践类任务 url 常缺 clazzid——缺班级号领不了卷，
            # 归"读不到（缺班级信息）"，不发无效请求（此前裸打 isExpire 把
            # json 原始报错 'Expecting value...' 喷进日志）。缓存，4h 内不重试。
            res = {"audited": True, "solvable": False,
                   "reason": "缺班级信息", "qreal": 0, "preview": ""}
            res["from_cache"] = False
            _cache_put(key, {k: v for k, v in res.items() if k != "from_cache"})
            return res
        else:
            qs, _ctx = questions.fetch_work_questions(
                client, task.get("courseId", ""), task.get("classId", ""),
                task.get("cpi", ""), task.get("workId", ""),
                answerid=task.get("answerId") or "0")
            if not qs:
                res = {"audited": True, "solvable": False, "reason": "无题",
                       "qreal": 0, "preview": ""}
            else:
                res = {"audited": True, "solvable": True, "reason": "",
                       "qreal": len(qs), "preview": _preview_of(qs)}
    except Exception as e:
        s = str(e)
        # 403/门槛/权限 = 稳定判定（4h 内不会变），缓存；网络抖动/超时/解析炸 = 瞬时，
        # 不缓存，留给下一轮刷新重试——绝不把一次断网灰锁 4 小时。
        gate = ("403" in s or "权限" in s or "任务点" in s)
        cacheable = gate
        res = {"audited": True, "solvable": False,
               "reason": "读不到题", "qreal": 0, "preview": "", "err": s[:80]}
    res["from_cache"] = False
    if cacheable:
        _cache_put(key, {k: v for k, v in res.items() if k != "from_cache"})
    return res
