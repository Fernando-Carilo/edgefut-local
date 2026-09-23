//! Shell Tauri do EdgeFut AI.
//!
//! Responsabilidades: iniciar o engine Python (sidecar `edgefut-engine`) em 127.0.0.1,
//! esperar `/health`, abrir a janela e encerrar o sidecar ao fechar. Nada além disso —
//! toda a lógica de análise vive no engine.

use std::net::TcpStream;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::Manager;
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

const ENGINE_PORT: u16 = 8765;

struct EngineProcess(Mutex<Option<CommandChild>>);

fn engine_is_up() -> bool {
    TcpStream::connect_timeout(&format!("127.0.0.1:{ENGINE_PORT}").parse().unwrap(), Duration::from_millis(300)).is_ok()
}

#[tauri::command]
fn engine_status() -> serde_json::Value {
    serde_json::json!({ "port": ENGINE_PORT, "host": "127.0.0.1", "up": engine_is_up() })
}

fn spawn_engine(app: &tauri::AppHandle) -> Option<CommandChild> {
    if engine_is_up() {
        log::info!("engine já está ativo em 127.0.0.1:{ENGINE_PORT} (modo dev?) — não iniciando sidecar");
        return None;
    }
    let cmd = match app.shell().sidecar("edgefut-engine") {
        Ok(c) => c.args(["--port", &ENGINE_PORT.to_string()]),
        Err(e) => {
            log::error!("sidecar edgefut-engine não encontrado: {e}");
            return None;
        }
    };
    match cmd.spawn() {
        Ok((mut rx, child)) => {
            tauri::async_runtime::spawn(async move {
                use tauri_plugin_shell::process::CommandEvent;
                while let Some(ev) = rx.recv().await {
                    match ev {
                        CommandEvent::Stdout(l) | CommandEvent::Stderr(l) => log::info!("[engine] {}", String::from_utf8_lossy(&l).trim_end()),
                        CommandEvent::Terminated(p) => log::warn!("[engine] terminou: {:?}", p.code),
                        _ => {}
                    }
                }
            });
            let start = Instant::now();
            while start.elapsed() < Duration::from_secs(40) && !engine_is_up() {
                std::thread::sleep(Duration::from_millis(250));
            }
            log::info!("engine {} após {:?}", if engine_is_up() { "pronto" } else { "ainda não respondeu" }, start.elapsed());
            Some(child)
        }
        Err(e) => {
            log::error!("falha ao iniciar sidecar: {e}");
            None
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .manage(EngineProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![engine_status])
        .setup(|app| {
            let child = spawn_engine(app.handle());
            *app.state::<EngineProcess>().0.lock().unwrap() = child;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(child) = window.app_handle().state::<EngineProcess>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("erro ao iniciar o EdgeFut AI");
}
