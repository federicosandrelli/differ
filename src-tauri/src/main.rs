#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem, MenuItemKind};
use tauri::{
    AppHandle, LogicalPosition, Manager, RunEvent, Theme, TitleBarStyle, Url, WebviewUrl,
    WebviewWindowBuilder, Wry,
};

struct Sidecar(Mutex<Option<Child>>);

// Marks the page as running in the app, so its CSS leaves room for the transparent title bar, and lays
// a drag region over that strip: the webview covers the title bar, so the window only moves when Tauri's
// drag handler sees a mousedown on an element with data-tauri-drag-region. The tabbed shell draws its
// tab strip there and marks it as the drag region itself, so it gets no overlay.
// The webview also drops clicks on target="_blank" links; sending them through the window lets
// on_navigation hand every URL outside the local server to the system browser.
const PAGE_SETUP: &str = r#"document.documentElement.classList.add('app');
document.addEventListener('DOMContentLoaded', () => {
  if (document.querySelector('[data-tauri-drag-region]')) return;
  const bar = document.createElement('div');
  bar.setAttribute('data-tauri-drag-region', '');
  bar.style.cssText = 'position:fixed;top:0;left:0;right:0;height:28px;z-index:2147483647;';
  document.body.prepend(bar);
});
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

const CLOSE_TAB: &str = "close-tab";
const CLOSE_WINDOW: &str = "close-window";

// Tauri's default menu binds Cmd+W to "Close Window", and closing the only window quits the app. This is the default
// menu with Cmd+W on "Close Tab", which tells the tab shell to close its active tab, and "Close Window" on Cmd+Shift+W.
fn app_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    let menu = Menu::default(app)?;
    for item in menu.items()? {
        let MenuItemKind::Submenu(submenu) = item else {
            continue;
        };
        let entries = submenu.items()?;
        for (i, entry) in entries.iter().enumerate() {
            let MenuItemKind::Predefined(close) = entry else {
                continue;
            };
            if close.text()? != "Close Window" {
                continue;
            }
            submenu.remove(close)?;
            if let Some(MenuItemKind::Predefined(separator)) =
                i.checked_sub(1).and_then(|j| entries.get(j))
            {
                if separator.text()?.is_empty() {
                    submenu.remove(separator)?;
                }
            }
        }
        if submenu.text()? == "File" {
            submenu.prepend_items(&[
                &MenuItem::with_id(app, CLOSE_TAB, "Close Tab", true, Some("CmdOrCtrl+W"))?,
                &MenuItem::with_id(
                    app,
                    CLOSE_WINDOW,
                    "Close Window",
                    true,
                    Some("CmdOrCtrl+Shift+W"),
                )?,
            ])?;
        }
    }
    Ok(menu)
}

fn main() {
    let _ = fix_path_env::fix();
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .menu(app_menu)
        .on_menu_event(|app, event| {
            let Some(window) = app.get_webview_window("main") else {
                return;
            };
            if event.id() == CLOSE_TAB {
                let _ = window.eval("window.dispatchEvent(new Event('close-tab'))");
            } else if event.id() == CLOSE_WINDOW {
                let _ = window.close();
            }
        })
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
                // Centred on the 40px tab strip the shell draws across the title bar.
                .traffic_light_position(LogicalPosition::new(13.0, 20.0))
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
