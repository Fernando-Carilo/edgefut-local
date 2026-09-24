//! Shell Tauri do EdgeFut AI.
//!
//! Responsabilidades: iniciar o engine Python (sidecar `edgefut-engine`) em 127.0.0.1,
//! esperar `/health`, abrir a janela e — iteração 5 (§46–48) — manter o coletor de dados
//! vivo em segundo plano: ícone na bandeja com a saúde do coletor e o último sync,
//! fechar a janela sem matar o sidecar (quando `background_collector` está ativo),
//! arranque opcional com o Windows (`autostart_on_login`) e "Sair" que encerra tudo.
//! Nada além disso — toda a lógica de análise vive no engine.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_autostart::ManagerExt as _;
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

const ENGINE_PORT: u16 = 8765;
const TRAY_ID: &str = "edgefut-tray";
const HEALTH_POLL: Duration = Duration::from_secs(60);
/// Argumento passado pelo autostart: arrancar escondido na bandeja.
const TRAY_ARG: &str = "--tray";

struct EngineProcess(Mutex<Option<CommandChild>>);

/// Espelho local das definições do engine que a shell precisa conhecer.
struct BackgroundPrefs {
    background_collector: AtomicBool,
    autostart_on_login: AtomicBool,
}

/// Último estado conhecido do coletor (para o tooltip/menu da bandeja).
#[derive(Clone, Default, serde::Serialize)]
struct CollectorStatus {
    health: String,
    last_fetched_at: Option<String>,
    stale_minutes: Option<f64>,
    reasons: Vec<String>,
    engine_up: bool,
}

struct TrayState {
    status: Mutex<CollectorStatus>,
    status_item: Mutex<Option<MenuItem<tauri::Wry>>>,
}

fn engine_is_up() -> bool {
    TcpStream::connect_timeout(&format!("127.0.0.1:{ENGINE_PORT}").parse().unwrap(), Duration::from_millis(300)).is_ok()
}

/// GET mínimo em HTTP/1.1 para 127.0.0.1 — evita puxar um cliente HTTP inteiro só para
/// dois endpoints locais. Devolve o corpo JSON.
fn local_get(path: &str) -> Option<serde_json::Value> {
    let mut s = TcpStream::connect_timeout(&format!("127.0.0.1:{ENGINE_PORT}").parse().ok()?, Duration::from_millis(800)).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(5))).ok()?;
    s.set_write_timeout(Some(Duration::from_secs(2))).ok()?;
    let req = format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{ENGINE_PORT}\r\nAccept: application/json\r\nConnection: close\r\n\r\n");
    s.write_all(req.as_bytes()).ok()?;
    let mut buf = Vec::with_capacity(8192);
    s.read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf);
    let (head, body) = text.split_once("\r\n\r\n")?;
    let status: u16 = head.lines().next()?.split_whitespace().nth(1)?.parse().ok()?;
    if status != 200 {
        return None;
    }
    // Chunked transfer não é usado pelo uvicorn com Connection: close + content-length,
    // mas por segurança tenta-se o corpo inteiro e, se falhar, o primeiro objeto JSON.
    serde_json::from_str(body).ok().or_else(|| body.find('{').and_then(|i| serde_json::from_str(&body[i..]).ok()))
}

fn fetch_collector_status() -> CollectorStatus {
    if !engine_is_up() {
        return CollectorStatus { health: "ENGINE OFFLINE".into(), engine_up: false, ..Default::default() };
    }
    match local_get("/flywheel/collector/health") {
        Some(v) => CollectorStatus {
            health: v.get("health").and_then(|x| x.as_str()).unwrap_or("UNKNOWN").to_string(),
            last_fetched_at: v.get("last_fetched_at").and_then(|x| x.as_str()).map(str::to_string),
            stale_minutes: v.get("stale_minutes").and_then(|x| x.as_f64()),
            reasons: v.get("reasons").and_then(|x| x.as_array()).map(|a| a.iter().filter_map(|r| r.as_str().map(str::to_string)).collect()).unwrap_or_default(),
            engine_up: true,
        },
        None => CollectorStatus { health: "UNKNOWN".into(), engine_up: true, ..Default::default() },
    }
}

fn load_prefs(prefs: &BackgroundPrefs) {
    if let Some(v) = local_get("/settings") {
        if let Some(b) = v.get("background_collector").and_then(|x| x.as_bool()) {
            prefs.background_collector.store(b, Ordering::Relaxed);
        }
        if let Some(b) = v.get("autostart_on_login").and_then(|x| x.as_bool()) {
            prefs.autostart_on_login.store(b, Ordering::Relaxed);
        }
    }
}

fn sync_autostart(app: &AppHandle, enabled: bool) {
    let mgr = app.autolaunch();
    let res = if enabled { mgr.enable() } else { mgr.disable() };
    match res {
        Ok(()) => log::info!("autostart {}", if enabled { "ativado" } else { "desativado" }),
        Err(e) => log::warn!("autostart: não foi possível aplicar ({e})"),
    }
}

fn status_line(s: &CollectorStatus) -> String {
    let sync = match (&s.last_fetched_at, s.stale_minutes) {
        (Some(_), Some(m)) if m < 1.0 => "último sync agora".to_string(),
        (Some(_), Some(m)) if m < 90.0 => format!("último sync há {} min", m.round() as i64),
        (Some(_), Some(m)) => format!("último sync há {:.1} h", m / 60.0),
        (Some(ts), None) => format!("último sync {ts}"),
        _ => "sem snapshot ainda".to_string(),
    };
    format!("Coletor: {} · {}", s.health, sync)
}

fn refresh_tray(app: &AppHandle) {
    let status = fetch_collector_status();
    let state = app.state::<TrayState>();
    if let Some(item) = state.status_item.lock().unwrap().as_ref() {
        let _ = item.set_text(status_line(&status));
    }
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let mut tip = format!("EdgeFut AI — {}", status_line(&status));
        if !status.reasons.is_empty() {
            tip.push_str("\n");
            tip.push_str(&status.reasons.join("; "));
        }
        let _ = tray.set_tooltip(Some(tip));
    }
    let _ = app.emit("collector-status", &status);
    *state.status.lock().unwrap() = status;
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn shutdown(app: &AppHandle) {
    if let Some(child) = app.state::<EngineProcess>().0.lock().unwrap().take() {
        log::info!("encerrando sidecar do engine");
        let _ = child.kill();
    }
    app.exit(0);
}

#[tauri::command]
fn engine_status() -> serde_json::Value {
    serde_json::json!({ "port": ENGINE_PORT, "host": "127.0.0.1", "up": engine_is_up() })
}

/// Estado atual do coletor tal como a bandeja o vê (o frontend usa o endpoint diretamente;
/// este comando existe para diagnóstico).
#[tauri::command]
fn collector_status(state: tauri::State<'_, TrayState>) -> CollectorStatus {
    state.status.lock().unwrap().clone()
}

/// Chamado pelo frontend depois de salvar Configurações: aplica close-to-tray e autostart
/// sem exigir reinício. Nunca guarda nada além destas duas flags.
#[tauri::command]
fn apply_background_settings(app: AppHandle, prefs: tauri::State<'_, BackgroundPrefs>, background_collector: bool, autostart_on_login: bool) -> serde_json::Value {
    prefs.background_collector.store(background_collector, Ordering::Relaxed);
    prefs.autostart_on_login.store(autostart_on_login, Ordering::Relaxed);
    sync_autostart(&app, autostart_on_login);
    let autostart_active = app.autolaunch().is_enabled().unwrap_or(false);
    serde_json::json!({ "background_collector": background_collector, "autostart_on_login": autostart_on_login, "autostart_active": autostart_active })
}

/// Encerra o app E o engine (equivalente a "Sair" na bandeja).
#[tauri::command]
fn quit_app(app: AppHandle) {
    shutdown(&app);
}

fn spawn_engine(app: &AppHandle) -> Option<CommandChild> {
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

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Abrir EdgeFut AI", true, None::<&str>)?;
    let status = MenuItem::with_id(app, "status", "Coletor: a verificar…", false, None::<&str>)?;
    let flywheel = MenuItem::with_id(app, "flywheel", "Ver Data Flywheel", true, None::<&str>)?;
    let refresh = MenuItem::with_id(app, "refresh", "Atualizar estado", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Sair (encerra o coletor)", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &status, &flywheel, &PredefinedMenuItem::separator(app)?, &refresh, &PredefinedMenuItem::separator(app)?, &quit])?;
    *app.state::<TrayState>().status_item.lock().unwrap() = Some(status);

    let mut builder = TrayIconBuilder::with_id(TRAY_ID).menu(&menu).show_menu_on_left_click(false).tooltip("EdgeFut AI — coletor em segundo plano");
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder
        .on_menu_event(|app, ev| match ev.id().as_ref() {
            "open" => show_main(app),
            "flywheel" => {
                show_main(app);
                let _ = app.emit("navigate", "/flywheel");
            }
            "refresh" => refresh_tray(app),
            "quit" => shutdown(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, ev| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = ev {
                show_main(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let start_in_tray = std::env::args().any(|a| a == TRAY_ARG);

    tauri::Builder::default()
        // Deve ser o primeiro plugin: com o app na bandeja, abrir de novo (atalho/autostart) só traz a
        // janela existente em vez de criar 2.ª instância, 2.º ícone e disputa pelo engine.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            log::info!("segunda instância pedida → mostrando a janela existente");
            show_main(app);
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(tauri_plugin_autostart::MacosLauncher::LaunchAgent, Some(vec![TRAY_ARG])))
        .manage(EngineProcess(Mutex::new(None)))
        .manage(BackgroundPrefs { background_collector: AtomicBool::new(true), autostart_on_login: AtomicBool::new(false) })
        .manage(TrayState { status: Mutex::new(CollectorStatus::default()), status_item: Mutex::new(None) })
        .invoke_handler(tauri::generate_handler![engine_status, collector_status, apply_background_settings, quit_app])
        .setup(move |app| {
            let handle = app.handle().clone();
            let child = spawn_engine(&handle);
            *app.state::<EngineProcess>().0.lock().unwrap() = child;

            // Preferências vêm do engine (fonte única de verdade); a shell só as espelha.
            let prefs = app.state::<BackgroundPrefs>();
            load_prefs(&prefs);
            sync_autostart(&handle, prefs.autostart_on_login.load(Ordering::Relaxed));

            build_tray(&handle)?;
            refresh_tray(&handle);

            if start_in_tray {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                    log::info!("arranque via autostart: janela escondida, coletor em segundo plano");
                }
            }

            // Poll periódico da saúde do coletor para a bandeja (mesmo com a janela fechada).
            let poll = handle.clone();
            std::thread::spawn(move || loop {
                std::thread::sleep(HEALTH_POLL);
                refresh_tray(&poll);
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != "main" {
                return;
            }
            match event {
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    let app = window.app_handle();
                    let keep = app.state::<BackgroundPrefs>().background_collector.load(Ordering::Relaxed);
                    if keep {
                        // §47: fechar a UI não mata o coletor — esconde na bandeja.
                        api.prevent_close();
                        let _ = window.hide();
                        log::info!("janela fechada → bandeja; engine e coletor continuam");
                    } else {
                        shutdown(app);
                    }
                }
                tauri::WindowEvent::Destroyed => {
                    let app = window.app_handle();
                    if !app.state::<BackgroundPrefs>().background_collector.load(Ordering::Relaxed) {
                        if let Some(child) = app.state::<EngineProcess>().0.lock().unwrap().take() {
                            let _ = child.kill();
                        }
                    }
                }
                _ => {}
            }
        })
        .build(tauri::generate_context!())
        .expect("erro ao iniciar o EdgeFut AI")
        .run(|app, event| {
            // Com a janela escondida o loop de eventos deve continuar: só "Sair" termina.
            if let tauri::RunEvent::ExitRequested { api, code, .. } = event {
                if code.is_none() && app.state::<BackgroundPrefs>().background_collector.load(Ordering::Relaxed) {
                    api.prevent_exit();
                }
            }
        });
}
