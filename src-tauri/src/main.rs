#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

mod update;

use tauri::menu::{Menu, MenuItem, MenuItemKind, PredefinedMenuItem};
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

fn log_dir() -> Option<PathBuf> {
    let dir = PathBuf::from(std::env::var_os("HOME")?).join("Library/Logs/differ");
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

// The server appends its stderr here, so a bug report from the app can point at the server side of a failure.
fn server_log() -> Option<PathBuf> {
    Some(log_dir()?.join("server.log"))
}

// Names the server log in every page, so the bug report can quote the path.
fn log_script(log: Option<&PathBuf>) -> String {
    match log {
        Some(path) => {
            let quoted = path
                .display()
                .to_string()
                .replace('\\', "\\\\")
                .replace('"', "\\\"");
            format!("window.differServerLog = \"{quoted}\";")
        }
        None => String::new(),
    }
}

const PYTHON_REQUIRED: (u32, u32) = (3, 13);

fn find_on_path(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(name))
        .find(|candidate| candidate.is_file())
}

// The Homebrew interpreter comes first: the login shell's PATH may put the Xcode python3 (3.9) or a
// pyenv shim ahead of it, and server.py needs 3.13.
fn python_command() -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(prefix) = std::env::var_os("HOMEBREW_PREFIX") {
        candidates.push(PathBuf::from(prefix).join("bin/python3.13"));
    }
    candidates.push(PathBuf::from("/opt/homebrew/bin/python3.13"));
    candidates.push(PathBuf::from("/usr/local/bin/python3.13"));
    candidates
        .into_iter()
        .find(|candidate| candidate.is_file())
        .or_else(|| find_on_path("python3.13"))
        .or_else(|| find_on_path("python3"))
}

fn python_version(python: &Path) -> Option<(u32, u32)> {
    let output = Command::new(python)
        .args([
            "-c",
            "import sys; print(sys.version_info[0], sys.version_info[1])",
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let mut parts = text.split_whitespace().map(|part| part.parse::<u32>().ok());
    Some((parts.next()??, parts.next()??))
}

fn checked_python() -> Result<PathBuf, String> {
    let Some(python) = python_command() else {
        return Err("No python3 was found.".to_string());
    };
    match python_version(&python) {
        Some(version) if version >= PYTHON_REQUIRED => Ok(python),
        Some((major, minor)) => Err(format!(
            "Found {} (Python {major}.{minor}).",
            python.display()
        )),
        None => Err(format!(
            "Found {}, but it did not report its version.",
            python.display()
        )),
    }
}

fn python_or_exit() -> PathBuf {
    match checked_python() {
        Ok(python) => python,
        Err(found) => {
            let (major, minor) = PYTHON_REQUIRED;
            let message = format!(
                "differ needs Python {major}.{minor} or newer. Install it with: brew install python@{major}.{minor}\n\n{found}"
            );
            eprintln!("{message}");
            rfd::MessageDialog::new()
                .set_level(rfd::MessageLevel::Error)
                .set_title("differ")
                .set_description(message)
                .set_buttons(rfd::MessageButtons::Ok)
                .show();
            std::process::exit(1);
        }
    }
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
const OPEN_SETTINGS: &str = "open-settings";

// Tauri's default menu binds Cmd+W to "Close Window", and closing the only window quits the app. This is the default
// menu with Cmd+W on "Close Tab", which tells the tab shell to close its active tab, and "Close Window" on Cmd+Shift+W,
// and with "Settings…" (Cmd+,) in the app menu after "About", which tells the shell to open its settings.
fn app_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    let menu = Menu::default(app)?;
    if let Some(MenuItemKind::Submenu(app_submenu)) = menu.items()?.first() {
        app_submenu.insert_items(
            &[
                &MenuItem::with_id(app, OPEN_SETTINGS, "Settings…", true, Some("CmdOrCtrl+,"))?,
                &PredefinedMenuItem::separator(app)?,
            ],
            2,
        )?;
    }
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
        .invoke_handler(tauri::generate_handler![
            update::update_check,
            update::update_run
        ])
        .menu(app_menu)
        .on_menu_event(|app, event| {
            let Some(window) = app.get_webview_window("main") else {
                return;
            };
            if event.id() == CLOSE_TAB {
                let _ = window.eval("window.dispatchEvent(new Event('close-tab'))");
            } else if event.id() == OPEN_SETTINGS {
                let _ = window.eval("window.dispatchEvent(new Event('open-settings'))");
            } else if event.id() == CLOSE_WINDOW {
                let _ = window.close();
            }
        })
        .setup(|app| {
            let python = python_or_exit();
            let port = pick_port()?;
            let log = server_log();
            let mut server_command = Command::new(python);
            // A __pycache__ written next to the bundled scripts would break the app's code signature.
            server_command
                .env("PYTHONDONTWRITEBYTECODE", "1")
                .arg(server_script(app)?)
                .arg(port.to_string())
                .arg("--exit-with-parent");
            if let Some(path) = &log {
                server_command.arg("--log").arg(path);
            }
            let child = server_command.spawn()?;
            app.manage(Sidecar(Mutex::new(Some(child))));

            let server: Url = format!("http://127.0.0.1:{port}/").parse().unwrap();
            let local = server.clone();
            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("differ")
                    .theme(Some(Theme::Dark))
                    .inner_size(1440.0, 900.0)
                    // The page's default 420px sidebar plus a diff pane wide enough for a whole file header.
                    .min_inner_size(1024.0, 640.0)
                    .title_bar_style(TitleBarStyle::Overlay)
                    .hidden_title(true)
                    // Centred on the 40px tab strip the shell draws across the title bar.
                    .traffic_light_position(LogicalPosition::new(13.0, 20.0))
                    .initialization_script(PAGE_SETUP)
                    .initialization_script_for_all_frames(log_script(log.as_ref()))
                    .on_navigation(move |target| {
                        let is_local =
                            target.host_str() == local.host_str() && target.port() == local.port();
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
