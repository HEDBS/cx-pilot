# -*- coding: utf-8 -*-
"""组装绿色发布包：sidecar 冻结件 + release 壳 → cx-pilot-v<ver>.zip。

流程（顺序有讲究）：
  1) 校验两个输入产物存在
  2) 组装到 <out>/cx-pilot-v<ver>/
  3) **先泄漏扫描组装好的目录**（含 exe/pyc 的 raw 字节层）——不过就**不出包**
  4) 打 zip（zip 是压缩的，扫不出明文，所以第 3 步必须在打包前做）
  5) 打印清单 + sha256

用法：
    python tools/make_release.py --version 0.4.0
    python tools/make_release.py --version 0.4.0 --out <输出目录> --no-scan
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_release_exe(explicit=None):
    """找 release 壳。候选顺序：显式 --shell-exe > CARGO_TARGET_DIR > 默认 target 目录。

    为什么要显式这个参数：脚本常在**新开的 shell** 里跑（没有 export CARGO_TARGET_DIR），
    而本机把 target 放在了 D 盘自定义目录 —— 只认默认路径就会误报"壳没编"。
    """
    cands = []
    if explicit:
        cands.append(explicit)
    ct = os.environ.get("CARGO_TARGET_DIR")
    if ct:
        cands.append(os.path.join(ct, "release", "shell.exe"))
    cands.append(os.path.join(ROOT, "shell", "src-tauri", "target", "release", "shell.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return c, cands
    return None, cands


QUICKSTART = """cx-pilot v{ver} — 学习通作业助手
================================================

怎么用
  1. 把整个文件夹解压到任意位置（别只把 exe 拖出来：sidecar\\ 是内置运行时，删了跑不起来）
  2. 双击 cx-pilot.exe
  3. 首次启动会弹「登录学习通」，输入手机号+密码（只存本机，不上传）
  4. 设置页配置一个解题后端。推荐硅基流动 siliconflow.cn：注册送额度、国内直连、不折腾代理
     （图片题另配一个视觉模型，不配也能用：公式题会落到审批屏让你看原图手填）
  5. 作业计划 → 刷新 → 勾选作业 → 开始解题 → 审批屏逐题过目 → 确认提交

要求
  Windows 10/11。需要系统自带的 WebView2（绝大多数机器已预装，没有就装一下）。
  不需要你装 Python —— sidecar\\ 里是内置运行时。

数据与隐私
  配置、cookie、答题记录都在 %APPDATA%\\cx-pilot\\，删掉这个文件夹即彻底清除。
  卸载 = 删文件夹，不留残留。

边界
  它只做你让它做的事：所有答案先进审批屏，你点「确认提交」才真的交，从不静默交卷。
  撞上超星的图片验证码会弹窗让你看一下验证码图、输 4 位（不自动识别验证码）。
  仅供个人学习管理与自动化技术研究使用。
"""


def _neutral(n):
    """等长的中性占位串：原地替换，绝不改变文件长度/结构。"""
    stem = b"X:\\build\\scrubbed\\"
    return (stem * (n // len(stem) + 1))[:n]


def scrub_local_paths(pkg, extra_roots=()):
    """擦掉发布产物里的**打包机本机路径**（等长原地替换）。

    为什么需要这一步：tauri-build 生成的 resource.rc 会把图标的**绝对路径**写进
    Windows 资源段（实测 release exe 里留了一份 `D:\\Hermes\\...\\src-tauri`）。
    那串文本运行时不使用（图标按资源 ID 加载），却会在公开仓的发行包里泄漏打包机路径。

    本函数不是"免死金牌"：擦完仍然要跑泄漏扫描，扫描才是判据（漏擦了就出不了包）。
    待擦前缀全部在运行时从环境推导，不写死任何本机路径。
    """
    roots = [ROOT, os.environ.get("CARGO_HOME"), os.environ.get("CARGO_TARGET_DIR"),
             os.environ.get("RUSTUP_HOME"), os.path.expanduser("~"),
             os.environ.get("USERPROFILE")] + list(extra_roots)
    pats = set()
    for r in roots:
        if not r:
            continue
        r = os.path.abspath(r)
        pats.add(r.encode("utf-8"))
        pats.add(r.replace("\\", "/").encode("utf-8"))
        pats.add(r.replace("/", "\\").encode("utf-8"))
    pats = sorted(pats, key=len, reverse=True)      # 长的先替，避免前缀互相打断

    changed = []
    for d, _s, fs in os.walk(pkg):
        for f in fs:
            p = os.path.join(d, f)
            try:
                raw = open(p, "rb").read()
            except OSError:
                continue
            new, n = raw, 0
            for pat in pats:
                if pat in new:
                    n += new.count(pat)
                    new = new.replace(pat, _neutral(len(pat)))
            if n:
                with open(p, "wb") as fh:
                    fh.write(new)
                changed.append((os.path.relpath(p, pkg), n))
    return changed


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="版本号，形如 0.4.0")
    ap.add_argument("--out", default=os.path.normpath(os.path.join(ROOT, "..", "cx-release")),
                    help="输出目录（默认仓库同级的 cx-release/，不写死盘符）")
    ap.add_argument("--no-scan", action="store_true", help="跳过泄漏扫描（仅本地调试用，发布别加）")
    ap.add_argument("--shell-exe", default=None,
                    help="release 壳路径（缺省按 CARGO_TARGET_DIR / 默认 target 找）")
    a = ap.parse_args()

    side_in = os.path.join(ROOT, "dist", "cx-sidecar")
    side_exe = os.path.join(side_in, "cx-sidecar.exe")
    shell_exe, tried = find_release_exe(a.shell_exe)
    if not os.path.isfile(side_exe):
        sys.exit("缺 sidecar 冻结件：%s\n先跑 python -m PyInstaller --noconfirm --clean sidecar.spec" % side_exe)
    if not shell_exe:
        sys.exit("缺 release 壳。找过这些位置：\n  " + "\n  ".join(tried) +
                 "\n先跑 cd shell/src-tauri && cargo build --release，" +
                 "或用 --shell-exe 直接指定")

    pkg = os.path.normpath(os.path.join(a.out, "cx-pilot-v" + a.version))
    if os.path.isdir(pkg):
        shutil.rmtree(pkg)
    os.makedirs(pkg)

    # 壳 → 根目录 cx-pilot.exe（productName 命名，用户双击的就是它）
    shutil.copy2(shell_exe, os.path.join(pkg, "cx-pilot.exe"))
    # 冻结运行时 → sidecar/（sidecar.rs 打包态就找 <exe_dir>/sidecar/cx-sidecar.exe）
    shutil.copytree(side_in, os.path.join(pkg, "sidecar"))
    # 文件名保持 ASCII：中文名在部分解压工具/资源管理器里会显示成乱码（内容仍是中文）
    with open(os.path.join(pkg, "README.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(QUICKSTART.format(ver=a.version))
    lic = os.path.join(ROOT, "LICENSE")
    if os.path.isfile(lic):
        shutil.copy2(lic, os.path.join(pkg, "LICENSE"))

    n_files = sum(len(fs) for _d, _s, fs in os.walk(pkg))
    size_mb = sum(os.path.getsize(os.path.join(d, f))
                  for d, _s, fs in os.walk(pkg) for f in fs) / 1048576.0
    print("组装完成: %s  (%d 文件, %.1f MB)" % (pkg, n_files, size_mb))

    # 擦打包机本机路径（tauri-build 的 resource.rc 会把图标绝对路径编进资源段）
    scrubbed = scrub_local_paths(pkg)
    if scrubbed:
        print("已擦除本机路径（等长替换）:")
        for rel, n in scrubbed[:12]:
            print("  %-44s %d 处" % (rel, n))
        if len(scrubbed) > 12:
            print("  ... 另 %d 个文件" % (len(scrubbed) - 12))

    # 泄漏扫描必须在打包前做：zip 压缩后扫不到明文
    if not a.no_scan:
        print("泄漏扫描（含 exe/pyc 字节层）...")
        rc = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "scan_leak.py"), pkg],
                            cwd=ROOT).returncode
        if rc != 0:
            sys.exit("!! 泄漏扫描未通过，不出包。处理上面命中的条目后重跑。")

    zpath = os.path.normpath(os.path.join(a.out, "cx-pilot-v%s.zip" % a.version))
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for d, _s, fs in os.walk(pkg):
            for f in fs:
                full = os.path.join(d, f)
                arc = os.path.join("cx-pilot-v" + a.version,
                                   os.path.relpath(full, pkg)).replace(os.sep, "/")
                z.write(full, arc)
    zmb = os.path.getsize(zpath) / 1048576.0
    print("\n出包: %s  (%.1f MB)" % (zpath, zmb))
    print("sha256: %s" % sha256(zpath))


if __name__ == "__main__":
    main()
