# -*- coding: utf-8 -*-
"""P1: 学习通签到层（mobilelearn.chaoxing.com）。

协议来源：社区实现 refs/xxtSignApi/README.md + 本机 2026-09-27 实测
  已实测有效：activelist（活动列表）、getPPTActiveInfo（签到详情/教师设定位置/签到码）
  未实测：newsign/preSign、pptSign/stuSignajax（提交参数）—— 所以提交一律默认 dry_run

三类现场证据（复刻要点）：
  位置：直接构造 latitude/longitude/address —— 服务端只拿它与 locationLatitude/locationLongitude
        算距离（locationRange 米），坐标是客户端上报值，不需要虚拟定位/GPS
  拍照：图片先传超星网盘 pan-yz.chaoxing.com 拿 objectId，再随签到提交
  二维码：enc 来自扫描教师投屏二维码；教师端可设 ewmRefreshTime 秒刷新

坐标系：主字段（locationLatitude/Longitude）实测为**百度 bd09**，
        另有 locationLatitude_gd/Longitude_gd 存高德 gcj02。提交用主字段那套。
"""
import json
import os
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass, field

from core.client import Client, DATA, UA

ML = "https://mobilelearn.chaoxing.com"
MOOC = "https://mooc1.chaoxing.com"

# 部分 mobilelearn 接口只认学习通 App UA（Windows Chrome UA 直接 500）
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0_3 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Mobile/15E148 (device:iPhone11,2) Language/zh-Hans "
             "com.ssreader.ChaoXingStudy/ChaoXingStudy_3_6.0.2_ios_phone_202209281930_99 (@Kalimdor)")

KIND_NAMES = {
    "normal": "普通签到",
    "photo": "拍照签到",
    "qrcode": "二维码签到",
    "location": "位置签到",
    "gesture": "手势签到",
    "code": "签到码签到",
    "unknown": "未知类型",
}


@dataclass
class SignActivity:
    active_id: str
    courseid: str = ""
    clazzid: str = ""
    title: str = ""
    kind: str = "unknown"          # 主类型（见 KIND_NAMES）
    other_id: int = 0              # 学习通原始判定字段
    needs_location: bool = False   # ifopenAddress==1
    needs_photo: bool = False      # ifphoto==1
    needs_face: bool = False       # openCheckFaceFlag==1
    needs_vcode: bool = False      # ifNeedVCode==1
    # —— 教师设定的位置（诉求③）——
    location_text: str = ""
    latitude: str = ""
    longitude: str = ""
    latitude_gd: str = ""
    longitude_gd: str = ""
    location_range: str = ""       # 允许距离（米）
    # —— 签到码/手势（诉求①）——
    sign_code: str = ""
    # —— 二维码 ——
    ewm_refresh_time: int = 0      # 教师端二维码刷新间隔（秒），0=不刷新
    # —— 时间与状态 ——
    start_str: str = ""
    end_str: str = ""
    late_end_str: str = ""
    late_minute: int = 0
    attend_num: int = 0
    status: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def kind_name(self):
        return KIND_NAMES.get(self.kind, self.kind)


def classify(other_id, if_photo, if_open_address):
    """类型判定。otherId 优先；位置/拍照是叠加属性，不是互斥类型。"""
    oid = int(other_id or 0)
    if oid == 2:
        return "qrcode"
    if oid == 3:
        return "gesture"
    if oid == 5:
        return "code"
    if oid == 4 or int(if_open_address or 0) == 1:
        return "location"
    if int(if_photo or 0) == 1:
        return "photo"
    return "normal"


def _uid(client):
    for ck in client.jar:
        if ck.name == "_uid":
            return ck.value
    return ""


def _ml_json(client, url, referer=ML + "/page/active/signIndex"):
    t = client.raw_get(url, referer=referer, headers={"User-Agent": MOBILE_UA})
    return json.loads(t.lstrip("@").strip())


def list_courses(client):
    """(courseId, classId, cpi, name) —— 复用 works 层的课程抠取。"""
    from core.works import _course_triplets
    return _course_triplets(client)


def list_activities(client, courseid, clazzid):
    """活动列表。实测：返回最近若干条（含已结束），比网页端"进行中"更全。"""
    ts = int(time.time() * 1000)
    url = ("%s/v2/apis/active/student/activelist?fid=0&courseId=%s&classId=%s"
           "&showNotStartedActive=0&_=%d" % (ML, courseid, clazzid, ts))
    j = _ml_json(client, url)
    data = j.get("data") or {}
    out = []
    for a in data.get("activeList") or []:
        out.append({
            "active_id": str(a.get("id")),
            "name": a.get("name"),
            "active_type": a.get("activeType"),
            "start": a.get("startTime"),
            "end": a.get("endTime"),
        })
    return out


def active_info(client, active_id, courseid="", clazzid=""):
    """签到详情 —— 教师设定位置、签到码、是否拍照/人脸/位置都在这里。"""
    j = _ml_json(client, "%s/v2/apis/active/getPPTActiveInfo?activeId=%s" % (ML, active_id))
    d = j.get("data") or {}
    if not d:
        raise RuntimeError("getPPTActiveInfo 无 data: %s" % j.get("msg"))
    a = SignActivity(
        active_id=str(d.get("id") or active_id),
        courseid=str(courseid or d.get("courseId") or ""),
        clazzid=str(clazzid or d.get("clazzid") or ""),
        title=str(d.get("name") or ""),
        other_id=int(d.get("otherId") or 0),
        needs_location=(int(d.get("ifopenAddress") or 0) == 1),
        needs_photo=(int(d.get("ifphoto") or 0) == 1),
        needs_face=(int(d.get("openCheckFaceFlag") or 0) == 1),
        needs_vcode=(int(d.get("ifNeedVCode") or 0) == 1),
        location_text=(d.get("locationText") or "").strip(),
        latitude=str(d.get("locationLatitude") or "").strip(),
        longitude=str(d.get("locationLongitude") or "").strip(),
        latitude_gd=str(d.get("locationLatitude_gd") or "").strip(),
        longitude_gd=str(d.get("locationLongitude_gd") or "").strip(),
        location_range=str(d.get("locationRange") or "").strip(),
        sign_code=str(d.get("signCode") or "").strip(),
        ewm_refresh_time=int(d.get("ewmRefreshTime") or 0),
        start_str=str(d.get("starttimeStr") or ""),
        end_str=str(d.get("endtimeStr") or ""),
        late_end_str=str(d.get("lateEndTime") or ""),
        late_minute=int(d.get("lateMinute") or 0),
        attend_num=int(d.get("attendNum") or 0),
        status=int(d.get("status") or 0),
        raw=d,
    )
    a.kind = classify(a.other_id, d.get("ifphoto"), d.get("ifopenAddress"))
    return a


# ---------------- 请求构造（干跑） ----------------

def pre_sign_url(client, a):
    """预签到：README 记录「所有签到前必须先预签到」。参数未实测。"""
    return ("%s/newsign/preSign?courseId=%s&classId=%s&activePrimaryId=%s"
            "&general=1&sys=1&ls=1&appType=15&tid=&uid=%s&ut=s"
            % (ML, a.courseid, a.clazzid, a.active_id, _uid(client)))


def submit_url(client, a, *, location=None, object_id=None, enc=None, app_type=15, fid="0"):
    """构造 stuSignajax（通用签到接口）。location=(text, lat, lng) 三选一覆盖教师位置。"""
    p = {"activeId": a.active_id}
    if a.kind == "qrcode":
        if not enc:
            raise ValueError("二维码签到必须提供 enc（扫描所得）")
        p["enc"] = enc
    if object_id:
        p["objectId"] = object_id
    if location:
        text, lat, lng = location
        p.update({"address": text, "latitude": str(lat), "longitude": str(lng)})
    elif a.needs_location:
        p.update({"address": a.location_text,
                  "latitude": a.latitude, "longitude": a.longitude})
    p.update({"fid": fid, "appType": app_type, "ifTiJiao": 1})
    return "%s/pptSign/stuSignajax?%s" % (ML, urllib.parse.urlencode(p))


def sign(client, a, *, dry_run=True, location=None, object_id=None, enc=None):
    """执行签到。dry_run=True（默认）只打印将发送的请求，不发。"""
    pre = pre_sign_url(client, a)
    sub = submit_url(client, a, location=location, object_id=object_id, enc=enc)
    print("  预签到 : %s" % pre)
    print("  提交   : %s" % sub)
    if dry_run:
        print("  [dry-run] 未发送。确认参数无误后传 dry_run=False 执行。")
        return None
    client.raw_get(pre, referer="%s/page/active/signIndex" % ML)
    time.sleep(0.4)
    body = client.raw_get(sub, referer="%s/page/active/signIndex" % ML)
    print("  服务端返回:", body[:200])
    return body


# ---------------- CLI（只读列出 / 干跑） ----------------

def _fmt(a):
    extra = []
    if a.needs_location:
        extra.append("位置必填[%s %s,%s ±%sm]" % (a.location_text or "未设", a.latitude or "-",
                                                  a.longitude or "-", a.location_range or "?"))
    if a.needs_photo:
        extra.append("需拍照")
    if a.needs_face:
        extra.append("需人脸")
    if a.needs_vcode:
        extra.append("需验证码")
    if a.sign_code:
        extra.append("签到码=%s" % a.sign_code)
    if a.ewm_refresh_time:
        extra.append("二维码%ds刷新" % a.ewm_refresh_time)
    if a.attend_num:
        extra.append("已签%d人" % a.attend_num)
    return "%-10s %s  %s  %s" % (a.kind_name, a.end_str, a.active_id, " ".join(extra))


if __name__ == "__main__":
    import sys
    c = Client()
    c.ensure_login()
    trips = list_courses(c)
    print("课程 %d 门，扫描签到活动…\n" % len(trips))
    found = 0
    for cid, cls, cpi, name in trips:
        try:
            acts = list_activities(c, cid, cls)
        except Exception as e:                                        # noqa: BLE001
            print("[%s] 列表失败: %s" % (name[:16], str(e)[:60]))
            continue
        if not acts:
            continue
        print("== %s (%d 条)" % (name[:24], len(acts)))
        for act in acts:
            try:
                a = active_info(c, act["active_id"], cid, cls)
            except Exception as e:                                    # noqa: BLE001
                print("   %s 详情失败: %s" % (act["active_id"], str(e)[:50]))
                continue
            print("   " + _fmt(a))
            found += 1
            if len(sys.argv) > 1 and sys.argv[1] == "--dry-run" and a.status != 2:
                sign(c, a, dry_run=True)
        time.sleep(0.5)
    print("\n共 %d 条活动详情。加 --dry-run 打印提交请求（不发送）。" % found)
