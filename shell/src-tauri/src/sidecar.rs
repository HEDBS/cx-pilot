//! cx-pilot sidecar 管理器：拉起 `python -m server`（dev）或内置 `sidecar/cx-sidecar.exe`（release），
//! 读 stdout 的 `CX_READY port=<n> token=<t>` 完成握手，进程退出时收尸。
//!
//! 解析顺序（安全：release 版不烘焙任何用户名路径）：
//!   1) env `CX_SIDECAR_BIN`（显式覆盖）+ 可选 `CX_PROJECT_DIR`（解释器目标缺省项目目录时：
//!      debug 回退仓库根，release 明错）
//!   2) 当前 exe 同目录 `sidecar/cx-sidecar.exe`（打包态，PyInstaller onedir）
//!   3) debug 构建专用（仅 debug_assertions，不进 release 二进制）：env `CX_PYTHON` →
//!      PATH 上 python/python3 → 最后才开发者本机 venv（运行时由 %LOCALAPPDATA% 拼出+exists 探测，
//!      二进制内无用户名路径字面量）+ `<repo>/` 项目根
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;

#[derive(Serialize, Clone)]
pub struct SidecarInfo {
    pub port: u16,
    pub token: String,
    pub mode: String,
}

pub struct Inner {
    pub info: Mutex<Option<SidecarInfo>>,
    pub child: Mutex<Option<Child>>,
}

#[derive(Clone)]
pub struct Sidecar(pub Arc<Inner>);

impl Sidecar {
    pub fn new() -> Self {
        Sidecar(Arc::new(Inner {
            info: Mutex::new(None),
            child: Mutex::new(None),
        }))
    }

    pub fn info(&self) -> Option<SidecarInfo> {
        self.0.info.lock().ok().and_then(|g| g.clone())
    }

    pub fn kill(&self) {
        if let Ok(mut g) = self.0.child.lock() {
            if let Some(mut c) = g.take() {
                let _ = c.kill();
                let _ = c.wait();
            }
        }
    }

    /// 阻塞直到握手完成或超时（默认 30s）。返回握手信息。
    pub fn start(&self) -> Result<SidecarInfo, String> {
        if let Some(i) = self.info() {
            return Ok(i);
        }
        let (prog, args, cwd, mode) = resolve_launch()?;
        let mut cmd = Command::new(&prog);
        cmd.args(&args)
            .current_dir(&cwd)
            .env("CX_PORT", "0")
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit()); // 不 pipe stderr：避免未读导致的阻塞

        let mut child = cmd
            .spawn()
            .map_err(|e| format!("拉起 sidecar 失败 [{mode}] {prog}: {e}"))?;

        let stdout = child.stdout.take().ok_or("sidecar stdout 缺失")?;
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let deadline = Instant::now() + Duration::from_secs(30);
        let mut port: u16 = 0;
        let mut token = String::new();

        loop {
            if Instant::now() > deadline {
                let _ = child.kill();
                return Err(format!("等 CX_READY 超时 30s（mode={mode}）"));
            }
            line.clear();
            if reader.read_line(&mut line).unwrap_or(0) == 0 {
                let _ = child.kill();
                return Err(format!("sidecar stdout 提前结束（mode={mode}），未见 CX_READY"));
            }
            if let Some(rest) = line.strip_prefix("CX_READY ") {
                for kv in rest.split_whitespace() {
                    if let Some(p) = kv.strip_prefix("port=") {
                        port = p.parse().unwrap_or(0);
                    }
                    if let Some(t) = kv.strip_prefix("token=") {
                        token = t.to_string();
                    }
                }
                if port > 0 && !token.is_empty() {
                    break;
                }
            }
        }

        // 后台抽干剩余 stdout，防止管道写满阻塞 sidecar（dev 可见其日志）
        std::thread::spawn(move || {
            let mut reader = reader;
            let mut l = String::new();
            loop {
                l.clear();
                if reader.read_line(&mut l).unwrap_or(0) == 0 {
                    break;
                }
                eprint!("[sidecar] {l}");
            }
        });

        let info = SidecarInfo {
            port,
            token,
            mode,
        };
        *self.0.child.lock().map_err(|_| "child 锁中毒")? = Some(child);
        *self.0.info.lock().map_err(|_| "info 锁中毒")? = Some(info.clone());
        Ok(info)
    }
}

fn resolve_launch() -> Result<(String, Vec<String>, PathBuf, String), String> {
    // 1) 显式 env：CX_SIDECAR_BIN 单设即生效（解释器名含 python → `-m server`，否则视为打包 sidecar）；
    //    项目目录取 CX_PROJECT_DIR，缺省时 python 目标在 debug 构建回退到仓库根、release 直接明错。
    if let Ok(bin) = std::env::var("CX_SIDECAR_BIN") {
        let bpath = PathBuf::from(&bin);
        let is_py = bpath
            .file_name()
            .map(|n| n.to_string_lossy().to_ascii_lowercase().starts_with("python"))
            .unwrap_or(false);
        let args: Vec<String> = if is_py {
            vec!["-m".into(), "server".into()]
        } else {
            vec![]
        };
        let cwd = match std::env::var("CX_PROJECT_DIR") {
            Ok(d) => PathBuf::from(d),
            Err(_) if !is_py => bpath.parent().unwrap_or(&PathBuf::from(".")).to_path_buf(),
            Err(_) => {
                #[cfg(debug_assertions)]
                { dev_project_root() }
                #[cfg(not(debug_assertions))]
                {
                    return Err(
                        "CX_SIDECAR_BIN 指向 python 解释器时必须同时设置 CX_PROJECT_DIR".into(),
                    );
                }
            }
        };
        return Ok((bin, args, cwd, "env".into()));
    }
    // 2) 打包态：<exe_dir>/sidecar/cx-sidecar.exe
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let cand = dir.join("sidecar").join("cx-sidecar.exe");
            if cand.exists() {
                let cwd = cand.parent().unwrap_or(dir).to_path_buf();
                return Ok((cand.to_string_lossy().into_owned(), vec![], cwd, "bundled".into()));
            }
        }
    }
    // 3) debug 专用 dev 回退
    #[cfg(debug_assertions)]
    {
        let py = dev_python();
        let root = dev_project_root();
        return Ok((py, vec!["-m".into(), "server".into()], root, "dev".into()));
    }
    #[allow(unreachable_code)]
    Err("release 版本未内置 sidecar/sidecar.exe（请在打包时放入 resources）".into())
}

#[cfg(debug_assertions)]
fn dev_python() -> String {
    // M3 C3 回退链（陌生机器可用优先，本机 dev 环境只作最后兜底）：
    //   1) env CX_PYTHON（显式指定，须存在）
    if let Ok(p) = std::env::var("CX_PYTHON") {
        if std::path::Path::new(&p).exists() {
            return p;
        }
    }
    //   2) PATH 上的 python / python3
    if let Some(p) = path_python() {
        return p;
    }
    //   3) 不硬编码任何开发机私有路径（公开仓须在陌生机器成立）
    "python".into() // 终极兜底：交给 spawn 按 PATH 解析，失败时报错含 mode=dev
}

/// 在 PATH 各目录中找 python/python3（含 .exe，非 Windows 直接命中裸名）。
#[cfg(debug_assertions)]
fn path_python() -> Option<String> {
    let exts: &[&str] = if cfg!(windows) { &["exe", ""] } else { &[""] };
    let path = std::env::var("PATH").unwrap_or_default();
    for dir in std::env::split_paths(&path) {
        for name in ["python", "python3"] {
            for ext in exts {
                let mut cand = dir.join(name);
                if !ext.is_empty() {
                    let mut s = cand.as_os_str().to_os_string();
                    s.push(".");
                    s.push(ext);
                    cand = PathBuf::from(s);
                }
                if cand.is_file() {
                    return Some(cand.to_string_lossy().into_owned());
                }
            }
        }
    }
    None
}

#[cfg(debug_assertions)]
fn dev_project_root() -> PathBuf {
    // CARGO_MANIFEST_DIR = <repo>/shell/src-tauri → 上溯两级
    let p = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..");
    p.canonicalize().unwrap_or_else(|_| PathBuf::from("."))
}