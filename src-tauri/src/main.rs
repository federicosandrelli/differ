#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, Theme, TitleBarStyle, Url, WebviewUrl, WebviewWindowBuilder};

struct Sidecar(Mutex<Option<Child>>);

// Marks the page as running in the app, so its CSS leaves room for the transparent title bar.
// The webview also drops clicks on target="_blank" links; sending them through the window lets
// on_navigation hand every URL outside the local server to the system browser.
const PAGE_SETUP: &str = r#"document.documentElement.classList.add('app');
document.addEventListener('click', e => {
  const a = e.target.closest('a[target="_blank"]');
  if (a && a.href) { e.preventDefault(); location.href = a.href; }
}, true);"#;

// The page keeps its settings (theme, reviewed marks, sidebar width) in localStorage, which the webview
// scopes to the origin, so the server needs the same port on every launch for them to survive.
const APP_PORT: u16 = 8766;

fn pick_port() -> std::io::Result<u16> {
    if TcpListener::bind(("127.0.0.1", APP_PORT)).is_ok() {
        return Ok(APP_PORT);
    }
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
}

fn server_script(app: &tauri::App) -> tauri::Result<PathBuf> {
    if cfg!(debug_assertions) {
        return Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../server.py"));
    }
    Ok(app.path().resource_dir()?.join("python/server.py"))
}

fn wait_for(port: u16, limit: Duration) -> bool {
    let addr = format!("127.0.0.1:{port}").parse().unwrap();
    let start = Instant::now();
    while start.elapsed() < limit {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    false
}

fn main() {
    let _ = fix_path_env::fix();
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let port = pick_port()?;
            let child = Command::new("python3")
                .arg(server_script(app)?)
                .arg(port.to_string())
                .arg("--exit-with-parent")
                .spawn()?;
            app.manage(Sidecar(Mutex::new(Some(child))));

            let server: Url = format!("http://127.0.0.1:{port}/").parse().unwrap();
            let local = server.clone();
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("differ")
                .theme(Some(Theme::Dark))
                .inner_size(1440.0, 900.0)
                .title_bar_style(TitleBarStyle::Overlay)
                .hidden_title(true)
                .initialization_script(PAGE_SETUP)
                .on_navigation(move |target| {
                    let is_local = target.host_str() == local.host_str() && target.port() == local.port();
                    if is_local || target.scheme() == "tauri" {
                        return true;
                    }
                    let _ = tauri_plugin_opener::open_url(target.as_str(), None::<&str>);
                    false
                })
                .build()?;

            std::thread::spawn(move || {
                if wait_for(port, Duration::from_secs(30)) {
                    let _ = window.navigate(server);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the app")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app.state::<Sidecar>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
