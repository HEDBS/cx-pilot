# -*- coding: utf-8 -*-
"""推送前泄漏审计：扫「会被 git 推送的全部文件」（含二进制 raw 字节）。

用法：python scan_leak.py
退出码 0 = 零命中；1 = 有命中（需处理后重跑）。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 脚本在 tools/，仓库根是上一层
SELF = os.path.relpath(os.path.abspath(__file__), ROOT).replace(os.sep, "/")

# ---- 待推送文件清单（把待推送内容暂存索引后再列，不产生提交）----
subprocess.run(["git", "add", "-A", "-N"], cwd=ROOT, capture_output=True)
out = subprocess.run(["git", "diff", "--cached", "--name-only"],
                     cwd=ROOT, capture_output=True, text=True).stdout
files = [f.strip() for f in out.splitlines() if f.strip()]
# 已跟踪文件也要扫（它们本就在公开仓里）
tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
files += [f.strip() for f in tracked.splitlines() if f.strip()]
files = sorted(set(files))

# ---- 泄漏模式（真课程名/账号/密钥/本机路径）----
PATTERNS = [
    (r"\b1[3-9]\d{9}\b", "手机号"),
    (r"sk-[A-Za-z0-9_.\-]{10,}", "API key"),
    (r"[Dd]:[\\/]Hermes", "本机路径 D:\\Hermes"),
    (r"[Cc]:[\\/]Users[\\/](?!<|\$)", "本机用户目录"),
    (r"hermes-agent", "hermes 内部路径"),
    (r"cx-pilot-run", "本机运行目录"),
    (r"@(qq|163|gmail|outlook|foxmail)\.com", "邮箱"),
    (r"\bHEDBS\b", "GitHub 账号名（若出现在非 URL 语境）"),
]
COURSE_WORDS = [
    "数据库原理", "马克思主义基本原理", "线性代数", "同济", "Java高级编程", "前端开发技术",
    "算法设计与分析", "大学物理", "软件开发安全", "形势与政策", "大学英语",
    "大学生心理健康", "网页设计基础", "体育（2）", "Linux操作",
    "Java编程基础", "河南科技",
]
# 说明：「随堂练习/自学测试/实验二/测验/考试/分组任务」是超星全平台通用的作业**类型名**，
# 不指向任何个人身份，故意不列入——列入会让扫描器永远报警、很快被无视（等于没有闸门）。

hits = []
for rel in files:
    if rel == SELF:      # 扫描器自带全部模式字面量，扫它必假阳性
        continue
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        continue
    try:
        raw = open(p, "rb").read()
    except Exception as e:
        hits.append((rel, 0, "读取失败", str(e)))
        continue
    text = raw.decode("utf-8", "replace")

    for pat, label in PATTERNS:
        for m in re.finditer(pat, text):
            s = max(0, m.start() - 40)
            hits.append((rel, text[:m.start()].count("\n") + 1, label, text[s:m.end() + 40].replace("\n", " ")))
    for w in COURSE_WORDS:
        for m in re.finditer(re.escape(w), text):
            s = max(0, m.start() - 50)
            hits.append((rel, text[:m.start()].count("\n") + 1, "真实课程名:" + w,
                         text[s:m.end() + 50].replace("\n", " ")))

print("扫描文件数:", len(files))
if not hits:
    print("LEAK-SCAN: 零命中 ✅")
    sys.exit(0)

# 去重并打印
seen = set()
print("LEAK-SCAN: 命中 %d 处 ⚠️" % len(hits))
for rel, ln, label, ctx in hits:
    key = (rel, label, ctx[:60])
    if key in seen:
        continue
    seen.add(key)
    print("  %-42s :%-5d [%s] ...%s..." % (rel, ln, label, ctx[:150]))
sys.exit(1)
