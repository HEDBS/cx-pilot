// cx-pilot Tauri 2 壳 —— M0 spike：拉起 Python sidecar 并把 (port, token) 交给前端。
mod sidecar;

use sidecar::{Sidecar, SidecarInfo};
use tauri::Manager;

#[tauri::command]
fn cx_info(sc: tauri::State<Sidecar>) -> Option<SidecarInfo> {
    sc.info()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Sidecar::new())
        .invoke_handler(tauri::generate_handler![cx_info])
        .setup(|app| {
            let sc = app.state::<Sidecar>().inner().clone();
            std::thread::spawn(move || match sc.start() {
                Ok(i) => println!("[cx] sidecar 就绪 mode={} port={}", i.mode, i.port),
                Err(e) => eprintln!("[cx] sidecar 启动失败: {e}"),
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri app")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(sc) = app.try_state::<Sidecar>() {
                    sc.kill(); // 退出时收掉 sidecar，绝不留孤儿进程
                }
            }
        });
}