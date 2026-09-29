# -*- coding: utf-8 -*-
"""推送前泄漏审计：扫「会被 git 推送的全部文件」（含二进制 raw 字节）。

用法：
    python tools/scan_leak.py                  # 扫仓库（git 暂存+已跟踪）
    python tools/scan_leak.py <路径> [<路径>…]  # 扫指定文件/目录（发布产物用，递归）
退出码 0 = 零命中；1 = 有命中（需处理后重跑）。

注：扫 zip **必须解包后扫**——zip 是压缩的，压缩流里 grep 不到明文。
tools/make_release.py 会调本脚本扫组装好的目录。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 脚本在 tools/，仓库根是上一层
SELF = os.path.relpath(os.path.abspath(__file__), ROOT).replace(os.sep, "/")

# ---- 待扫描文件清单 ----
ARGV = [a for a in sys.argv[1:] if not a.startswith("-")]
if ARGV:
    # 显式路径模式（发布产物）：递归收集，路径按传入的绝对/相对形式显示
    files = []
    for a in ARGV:
        if os.path.isdir(a):
            for dirpath, _dirnames, names in os.walk(a):
                for n in names:
                    files.append(os.path.join(dirpath, n))
        elif os.path.isfile(a):
            files.append(a)
    files = sorted(set(files))
    BASE = None            # 显示时用原样路径
else:
    # 仓库模式：暂存索引后再列（不产生提交）+ 已跟踪文件
    BASE = ROOT
    subprocess.run(["git", "add", "-A", "-N"], cwd=ROOT, capture_output=True)
    out = subprocess.run(["git", "diff", "--cached", "--name-only"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    files = [f.strip() for f in out.splitlines() if f.strip()]
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    files += [f.strip() for f in tracked.splitlines() if f.strip()]
    files = sorted(set(files))

# ---- 泄漏模式（真课程名/账号/密钥/本机路径）----
LOCAL_USER = (os.environ.get("USERNAME") or os.environ.get("USER") or "").strip()

PATTERNS = [
    (r"\b1[3-9]\d{9}\b", "手机号"),
    (r"sk-[A-Za-z0-9_.\-]{10,}", "API key"),
    (r"[Dd]:[\\/]Hermes", "本机路径 D:\\Hermes"),
    # 本机用户目录：只对**当前用户**报硬命中。
    # 为什么不用 `C:\Users\<任意>`：第三方冻结件里普遍带**构建机**路径
    # （runneradmin / Administrator / 各家 CI 用户），那不是用户身份，
    # 却会一次刷出 200+ 条命中，把真泄漏淹掉 —— 闸门随即被无视（等于没有）。
    (r"[Cc]:[\\/]Users[\\/]" + (re.escape(LOCAL_USER) if LOCAL_USER else r"[^\\/\s]+"),
     "本机用户目录"),
    (r"hermes-agent", "hermes 内部路径"),
    (r"cx-pilot-run", "本机运行目录"),
    (r"@(qq|163|gmail|outlook|foxmail)\.com", "邮箱"),
    (r"\bHEDBS\b", "GitHub 账号名（若出现在非 URL 语境）"),
]
# 第三方构件（PyInstaller 冻结的依赖、node_modules）里，通用 PII 模式必然全是噪声：
# 依赖作者邮箱、二进制里随机字节凑出的"手机号"。对这些目录只保留指向**本机身份**的模式。
THIRD_PARTY = ("_internal/", "_internal\\", "node_modules/", "node_modules\\",
               "site-packages/", "site-packages\\")
SOFT_IN_3RD = {"邮箱", "手机号"}
COURSE_WORDS = [
    "数据库原理", "马克思主义基本原理", "线性代数", "同济", "Java高级编程", "前端开发技术",
    "算法设计与分析", "大学物理", "软件开发安全", "形势与政策", "大学英语",
    "大学生心理健康", "网页设计基础", "体育（2）", "Linux操作",
    "Java编程基础", "河南科技",
]
# 说明：「随堂练习/自学测试/实验二/测验/考试/分组任务」是超星全平台通用的作业**类型名**，
# 不指向任何个人身份，故意不列入——列入会让扫描器永远报警、很快被无视（等于没有闸门）。

hits = []
skipped = [0]        # 第三方构件里被跳过的通用 PII 噪声条数（仅报告，不算命中）
for rel in files:
    # 仓库模式下跳过扫描器自身（它的模式字面量必然自命中）；显式路径模式下按 basename 同样跳过
    if os.path.basename(str(rel)) == os.path.basename(SELF):
        continue
    p = rel if ARGV else os.path.join(ROOT, str(rel).replace("/", os.sep))
    if not os.path.isfile(p):
        continue
    name = str(rel) if ARGV else rel
    try:
        raw = open(p, "rb").read()
    except Exception as e:
        hits.append((name, 0, "读取失败", str(e)))
        continue
    text = raw.decode("utf-8", "replace")
    third = any(t in name for t in THIRD_PARTY)

    for pat, label in PATTERNS:
        if third and label in SOFT_IN_3RD:
            # 第三方构件里的作者邮箱 / 随机字节凑出的"手机号"：计数但不算命中
            skipped[0] += 1
            continue
        for m in re.finditer(pat, text):
            s = max(0, m.start() - 40)
            hits.append((name, text[:m.start()].count("\n") + 1, label, text[s:m.end() + 40].replace("\n", " ")))
    for w in COURSE_WORDS:
        for m in re.finditer(re.escape(w), text):
            s = max(0, m.start() - 50)
            hits.append((name, text[:m.start()].count("\n") + 1, "真实课程名:" + w,
                         text[s:m.end() + 50].replace("\n", " ")))

print("扫描文件数:", len(files))
if skipped[0]:
    print("（第三方构件内跳过 %d 条通用 PII 噪声：依赖作者邮箱 / 二进制随机字节）" % skipped[0])
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
