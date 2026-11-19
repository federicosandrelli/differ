#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::net::{TcpListener, TcpStream};
#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU16, Ordering};
use std::sync::Mutex;
use std::time::Duration;

mod update;

use serde::Serialize;
use tauri::menu::{Menu, MenuItem, MenuItemKind, PredefinedMenuItem};
use tauri::{
    AppHandle, Manager, RunEvent, State, Theme, Url, WebviewUrl, WebviewWindowBuilder, Wry,
};
#[cfg(target_os = "macos")]
use tauri::{LogicalPosition, TitleBarStyle};
use tauri_plugin_deep_link::DeepLinkExt;

struct Sidecar {
    python: PathBuf,
    script: PathBuf,
    port: AtomicU16,
    child: Mutex<Option<Child>>,
}

// What a differ:// link asks for: a pull request, and a prompt to put into its Claude session's prompt, unsent.
#[derive(Clone, Debug, PartialEq, Serialize)]
struct Link {
    pr: String,
    prompt: Option<String>,
}

// The links that arrived, held until the shell takes them: a link that launches the app arrives before the shell
// has loaded.
struct Links(Mutex<Vec<Link>>);

// Marks the page as running in the app. WebView2 runs this before the page has an <html> element, so there the mark
// waits for it. The webview drops clicks on target="_blank" links; sending them through the window lets on_navigation
// hand every URL outside the local server to the system browser.
const PAGE_SETUP: &str = r#"const markApp = () => document.documentElement.classList.add('app');
if (document.documentElement) markApp();
else new MutationObserver((_, observer) => {
  if (document.documentElement) { markApp(); observer.disconnect(); }
}).observe(document, { childList: true });
document.addEventListener('click', e => {
  const a = e.target.closest('a[target="_blank"]');
  if (a && a.href) { e.preventDefault(); location.href = a.href; }
}, true);"#;

// On macOS the webview covers a transparent title bar; elsewhere the system's stays. Marks the page, so its CSS
// leaves room for that strip, and lays a drag region over it: the window only moves when Tauri's drag handler
// sees a mousedown on an element with data-tauri-drag-region. The tabbed shell draws its tab strip there and
// marks it as the drag region itself, so it gets no overlay.
#[cfg(target_os = "macos")]
const TITLE_BAR_SETUP: &str = r#"document.documentElement.classList.add('titlebar');
document.addEventListener('DOMContentLoaded', () => {
  if (document.querySelector('[data-tauri-drag-region]')) return;
  const bar = document.createElement('div');
  bar.setAttribute('data-tauri-drag-region', '');
  bar.style.cssText = 'position:fixed;top:0;left:0;right:0;height:28px;z-index:2147483647;';
  document.body.prepend(bar);
});"#;

// The page keeps its settings (theme, reviewed marks, sidebar width) in localStorage, which the webview
// scopes to the origin, so the server needs the same port on every launch for them to survive.
const APP_PORT: u16 = 8766;

// Rust binds with SO_REUSEADDR, so a bind to 127.0.0.1 succeeds even while another server listens on the port
// on [::] or ::1. server.py then refuses the port, and the window would load that other server.
fn answers(port: u16) -> bool {
    ["127.0.0.1", "[::1]"].iter().any(|host| {
        let addr = format!("{host}:{port}").parse().unwrap();
        TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
    })
}

fn pick_port() -> std::io::Result<u16> {
    if !answers(APP_PORT) && TcpListener::bind(("127.0.0.1", APP_PORT)).is_ok() {
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
    // Not %LOCALAPPDATA%\differ: the installer puts the app there, and the uninstaller only removes that folder empty.
    let dir = if cfg!(windows) {
        PathBuf::from(std::env::var_os("LOCALAPPDATA")?).join(r"com.federicosandrelli.differ\logs")
    } else {
        PathBuf::from(std::env::var_os("HOME")?).join("Library/Logs/differ")
    };
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

fn server_log() -> Option<PathBuf> {
    Some(log_dir()?.join("server.log"))
}

const PYTHON_REQUIRED: (u32, u32) = (3, 13);

// python.exe is a console program: started from an app without a console, each run would open a console window.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn find_on_path(name: &str) -> impl Iterator<Item = PathBuf> {
    let path = std::env::var_os("PATH").unwrap_or_default();
    std::env::split_paths(&path)
        .map(move |dir| dir.join(name))
        .collect::<Vec<_>>()
        .into_iter()
}

// The Homebrew interpreter comes first: the login shell's PATH may put the Xcode python3 (3.9) or a
// pyenv shim ahead of it, and server.py needs 3.13.
#[cfg(unix)]
fn python_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(prefix) = std::env::var_os("HOMEBREW_PREFIX") {
        candidates.push(PathBuf::from(prefix).join("bin/python3.13"));
    }
    candidates.push(PathBuf::from("/opt/homebrew/bin/python3.13"));
    candidates.push(PathBuf::from("/usr/local/bin/python3.13"));
    candidates.extend(find_on_path("python3.13"));
    candidates.extend(find_on_path("python3"));
    candidates
}

// Where the python.org installer and winget put Python for one user, where uv puts it, then the PATH. These folders
// and the PATH also hold older versions, so the choice falls to the version check. The python.exe that Windows puts in
// WindowsApps while no Python is installed only says how to get one.
#[cfg(windows)]
fn python_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        candidates.extend(newest_first(
            &PathBuf::from(local).join(r"Programs\Python"),
            "Python",
        ));
    }
    if let Some(home) = std::env::var_os("USERPROFILE") {
        candidates.push(PathBuf::from(home).join(r".local\bin\python3.13.exe"));
    }
    if let Some(roaming) = std::env::var_os("APPDATA") {
        candidates.extend(newest_first(
            &PathBuf::from(roaming).join(r"uv\python"),
            "cpython-",
        ));
    }
    candidates.extend(find_on_path("python3.13.exe"));
    candidates.extend(
        find_on_path("python.exe")
            .filter(|python| !python.to_string_lossy().contains("WindowsApps")),
    );
    candidates
}

// The python.exe of each folder in `dir` whose name starts with `prefix`, the newest first.
#[cfg(windows)]
fn newest_first(dir: &Path, prefix: &str) -> Vec<PathBuf> {
    let names = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.file_name().to_string_lossy().into_owned());
    by_version(names, prefix)
        .into_iter()
        .map(|name| dir.join(name).join("python.exe"))
        .collect()
}

// The names that start with `prefix`, the highest version first. The version is the numbers right after the prefix,
// up to the next hyphen: Python313 and Python313-32 are both 3.13, and cpython-3.13.1-windows-x86_64-none is 3.13.1.
#[cfg(windows)]
fn by_version(names: impl Iterator<Item = String>, prefix: &str) -> Vec<String> {
    let version = |name: &str| -> Vec<u32> {
        name[prefix.len()..]
            .split('-')
            .next()
            .unwrap_or_default()
            .split('.')
            .filter_map(|part| part.parse().ok())
            .collect()
    };
    let mut names: Vec<_> = names.filter(|name| name.starts_with(prefix)).collect();
    names.sort_by_key(|name| std::cmp::Reverse(version(name)));
    names
}

const PYTHON_INSTALL: &str = if cfg!(windows) {
    "winget install Python.Python."
} else {
    "brew install python@"
};

// The version a Python runs as, and the interpreter to start. On Windows that is the one it reports, since a uv shim
// or a virtual environment's python.exe starts the interpreter as a process of its own, and the server must be the
// app's child to see the app go.
fn python_version(python: &Path) -> Option<((u32, u32), PathBuf)> {
    let mut command = Command::new(python);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = command
        .args([
            "-c",
            "import sys; print(*sys.version_info[:2]); print(sys._base_executable)",
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let mut lines = text.lines();
    let mut parts = lines
        .next()?
        .split_whitespace()
        .map(|part| part.parse::<u32>().ok());
    let version = (parts.next()??, parts.next()??);
    let interpreter = match lines.next() {
        Some(base) if cfg!(windows) && !base.is_empty() => PathBuf::from(base),
        _ => python.to_path_buf(),
    };
    Some((version, interpreter))
}

// The first candidate that runs as the version server.py needs, or what the first one found turned out to be.
fn checked_python() -> Result<PathBuf, String> {
    let candidates: Vec<_> = python_candidates()
        .into_iter()
        .filter(|candidate| candidate.is_file())
        .collect();
    let mut found = None;
    for python in &candidates {
        let version = python_version(python);
        if let Some((v, interpreter)) = &version {
            if *v >= PYTHON_REQUIRED {
                return Ok(interpreter.clone());
            }
        }
        found.get_or_insert((python, version));
    }
    Err(match found {
        None => "No python3 was found.".to_string(),
        Some((python, Some(((major, minor), _)))) => {
            format!("Found {} (Python {major}.{minor}).", python.display())
        }
        Some((python, None)) => format!(
            "Found {}, but it did not report its version.",
            python.display()
        ),
    })
}

fn python_or_exit() -> PathBuf {
    match checked_python() {
        Ok(python) => python,
        Err(found) => {
            let (major, minor) = PYTHON_REQUIRED;
            let message = format!(
                "differ needs Python {major}.{minor} or newer. Install it with: {PYTHON_INSTALL}{major}.{minor}\n\n{found}"
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

// Opens the server in the window once it prints its first line, which says it serves: a port that merely answers
// may belong to another server.
fn start_server(app: &AppHandle) -> std::io::Result<()> {
    let sidecar = app.state::<Sidecar>();
    let port = pick_port()?;
    let mut command = Command::new(&sidecar.python);
    // A __pycache__ written next to the bundled scripts would break the app's code signature. On POSIX the server
    // gets a process group of its own, so that `end_server` can end it with whatever it started in its group.
    command
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .arg(&sidecar.script)
        .arg(port.to_string())
        .arg("--exit-with-parent")
        .stdout(Stdio::piped());
    #[cfg(unix)]
    command.process_group(0);
    // An app without a console hands its children invalid standard handles, and the server's writes to an invalid
    // stderr fail every request it answers; the null device takes them until --log sends stderr to the log.
    #[cfg(windows)]
    command
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stderr(Stdio::null());
    if let Some(path) = server_log() {
        command.arg("--log").arg(path);
    }
    let mut child = command.spawn()?;
    let stdout = child.stdout.take().expect("stdout is piped");
    sidecar.port.store(port, Ordering::Relaxed);
    *sidecar.child.lock().unwrap() = Some(child);
    let app = app.clone();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        if reader
            .read_line(&mut String::new())
            .is_ok_and(|read| read > 0)
        {
            if let Some(window) = app.get_webview_window("main") {
                let url: Url = format!("http://127.0.0.1:{port}/").parse().unwrap();
                let _ = window.navigate(url);
            }
        }
        // The server would block on a full pipe.
        let _ = std::io::copy(&mut reader, &mut std::io::sink());
    });
    Ok(())
}

#[tauri::command]
fn server_failed(sidecar: State<Sidecar>) -> bool {
    let mut child = sidecar.child.lock().unwrap();
    child
        .as_mut()
        .is_none_or(|child| !matches!(child.try_wait(), Ok(None)))
}

#[tauri::command]
fn retry_server(app: AppHandle) -> Result<(), String> {
    if !server_failed(app.state()) {
        return Ok(());
    }
    start_server(&app).map_err(|err| err.to_string())
}

// The webview answers window.confirm with false at once and shows no dialog, so the shell asks the reader here.
#[tauri::command]
async fn confirm(window: tauri::WebviewWindow, message: String) -> bool {
    rfd::AsyncMessageDialog::new()
        .set_parent(&window)
        .set_level(rfd::MessageLevel::Warning)
        .set_title("differ")
        .set_description(message)
        .set_buttons(rfd::MessageButtons::OkCancel)
        .show()
        .await
        == rfd::MessageDialogResult::Ok
}

#[tauri::command]
fn take_links(links: State<Links>) -> Vec<Link> {
    std::mem::take(&mut *links.0.lock().unwrap())
}

// The links among the URLs: differ://open?pr=<a pull request URL or owner/repo#123>, with &prompt=<text> for one
// that brings a prompt.
fn links_of(urls: &[Url]) -> Vec<Link> {
    urls.iter()
        .filter(|url| url.scheme() == "differ" && url.host_str() == Some("open"))
        .filter_map(|url| {
            // A # in the link is text, such as the # of an unencoded owner/repo#123, not the start of a fragment.
            let mut query = url.query().unwrap_or("").to_owned();
            if let Some(fragment) = url.fragment() {
                query = format!("{query}#{fragment}");
            }
            let parsed: Url = format!("differ://open?{}", query.replace('#', "%23"))
                .parse()
                .ok()?;
            let value = |name: &str| {
                parsed
                    .query_pairs()
                    .find(|(key, _)| key == name)
                    .map(|(_, value)| value.into_owned())
            };
            Some(Link {
                pr: value("pr")?,
                prompt: value("prompt").filter(|prompt| !prompt.trim().is_empty()),
            })
        })
        .collect()
}

// A differ:// link opens its pull request in a tab. The app keeps the link and tells the shell, which takes it with
// take_links.
fn open_links(app: &AppHandle, urls: &[Url]) {
    let links = links_of(urls);
    if links.is_empty() {
        return;
    }
    app.state::<Links>().0.lock().unwrap().extend(links);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.set_focus();
        let _ = window.eval("window.dispatchEvent(new Event('links'))");
    }
}

const CLOSE_TAB: &str = "close-tab";
const CLOSE_WINDOW: &str = "close-window";
const OPEN_SETTINGS: &str = "open-settings";

// Tauri's default menu binds Cmd+W to "Close Window", and closing the only window quits the app. This is the default
// menu with Cmd+W on "Close Tab", which tells the tab shell to close its active tab, and "Close Window" on Cmd+Shift+W,
// and with "Settings…" (Cmd+,) in the app menu after "About", which tells the shell to open its settings. Windows has
// no app menu, so there "Settings…" opens the File menu, and its default menu calls "Close Window" "Close".
fn app_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    const PREDEFINED_CLOSE: &str = if cfg!(windows) {
        "Close"
    } else {
        "Close Window"
    };
    let menu = Menu::default(app)?;
    #[cfg(target_os = "macos")]
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
            if close.text()? != PREDEFINED_CLOSE {
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
            #[cfg(not(target_os = "macos"))]
            submenu.insert_items(
                &[
                    &PredefinedMenuItem::separator(app)?,
                    &MenuItem::with_id(app, OPEN_SETTINGS, "Settings…", true, Some("CmdOrCtrl+,"))?,
                    &PredefinedMenuItem::separator(app)?,
                ],
                2,
            )?;
        }
    }
    Ok(menu)
}

fn main() {
    let _ = fix_path_env::fix();
    let builder = tauri::Builder::default();
    // Windows starts another copy of the app for each differ:// link; this one hands the link to the copy that runs,
    // through the deep-link plugin, and quits. It goes first, before anything else starts.
    #[cfg(windows)]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.unminimize();
            let _ = window.set_focus();
        }
    }));
    builder
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_deep_link::init())
        .invoke_handler(tauri::generate_handler![
            update::update_check,
            update::update_run,
            server_failed,
            retry_server,
            confirm,
            take_links
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
            app.manage(Sidecar {
                python: python_or_exit(),
                script: server_script(app)?,
                port: AtomicU16::new(0),
                child: Mutex::new(None),
            });
            app.manage(Links(Mutex::new(Vec::new())));
            let linked = app.handle().clone();
            app.deep_link()
                .on_open_url(move |event| open_links(&linked, &event.urls()));
            // macOS hands over the link that launches the app before setup runs, so no listener hears it.
            if let Ok(Some(urls)) = app.deep_link().get_current() {
                open_links(app.handle(), &urls);
            }
            let handle = app.handle().clone();
            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("differ")
                    .theme(Some(Theme::Dark))
                    .inner_size(1440.0, 900.0)
                    // The page's default 420px sidebar plus a diff pane wide enough for a whole file header.
                    .min_inner_size(1024.0, 640.0)
                    .initialization_script(PAGE_SETUP);
            #[cfg(target_os = "macos")]
            let window = window
                .title_bar_style(TitleBarStyle::Overlay)
                .hidden_title(true)
                // Centred on the 40px tab strip the shell draws across the title bar.
                .traffic_light_position(LogicalPosition::new(13.0, 20.0))
                .initialization_script(TITLE_BAR_SETUP);
            window
                .on_navigation(move |target| {
                    let port = handle.state::<Sidecar>().port.load(Ordering::Relaxed);
                    let is_local =
                        target.host_str() == Some("127.0.0.1") && target.port() == Some(port);
                    if is_local || target.scheme() == "tauri" {
                        return true;
                    }
                    let _ = tauri_plugin_opener::open_url(target.as_str(), None::<&str>);
                    false
                })
                .build()?;
            if let Err(err) = start_server(app.handle()) {
                eprintln!("The review server did not start: {err}");
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the app")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app.state::<Sidecar>().child.lock().unwrap().take() {
                    end_server(&mut child);
                }
            }
        });
}

// Ends the server: SIGTERM to its process group, which the server answers by ending the Claude sessions it runs and
// exiting, then SIGKILL to whatever is left after ten seconds. Each session has a process group of its own, so only
// the server ends the sessions, and it gets the time its signals to them take: three for the hangup, two for
// SIGTERM and two for SIGKILL.
#[cfg(unix)]
fn end_server(child: &mut Child) {
    let group = child.id() as libc::pid_t;
    unsafe {
        libc::killpg(group, libc::SIGTERM);
    }
    for _ in 0..100 {
        if !matches!(child.try_wait(), Ok(None)) {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    unsafe {
        libc::killpg(group, libc::SIGKILL);
    }
    let _ = child.wait();
}

// Ends the server. Windows has no signal for it to answer, but each Claude session runs in a job object that ends
// its processes once the last handle to it closes, and the server's handles close as it ends.
#[cfg(windows)]
fn end_server(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(test)]
mod tests {
    use super::{answers, links_of, Link};
    use std::net::TcpListener;
    use tauri::Url;

    #[test]
    fn takes_the_pull_request_and_prompt_of_differ_open_links() {
        let urls: Vec<Url> = [
            "differ://open?pr=https%3A%2F%2Fgithub.com%2Fo%2Fr%2Fpull%2F1",
            "differ://open?pr=o/r#2",
            "differ://open?pr=o/r%233",
            "differ://settings?pr=o/r%234",
            "other://open?pr=o/r%235",
            "differ://open",
            "differ://open?pr=o/r%236&prompt=%2Freview-pr%20now",
            "differ://open?pr=o/r#7&prompt=a#b",
            "differ://open?pr=o/r%238&prompt=%20",
        ]
        .iter()
        .map(|url| url.parse().unwrap())
        .collect();
        let link = |pr: &str, prompt: Option<&str>| Link {
            pr: pr.to_owned(),
            prompt: prompt.map(str::to_owned),
        };
        assert_eq!(
            links_of(&urls),
            [
                link("https://github.com/o/r/pull/1", None),
                link("o/r#2", None),
                link("o/r#3", None),
                link("o/r#6", Some("/review-pr now")),
                link("o/r#7", Some("a#b")),
                link("o/r#8", None),
            ]
        );
    }

    #[test]
    fn sees_a_server_that_listens_on_ipv6_only() {
        let listener = TcpListener::bind("[::1]:0").unwrap();
        assert!(answers(listener.local_addr().unwrap().port()));
    }

    #[test]
    fn sees_a_free_port() {
        let port = TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        assert!(!answers(port));
    }
    #[cfg(windows)]
    #[test]
    fn orders_python_folders_by_the_version_after_their_prefix() {
        // As the folder listing gives them: by name.
        let names = [
            "Launcher",
            "Python310",
            "Python313",
            "Python313-32",
            "Python39",
            "cpython-3.13.18-windows-x86_64-none",
        ];
        let sorted = super::by_version(names.iter().map(|name| name.to_string()), "Python");
        assert_eq!(
            sorted,
            ["Python313", "Python313-32", "Python310", "Python39"]
        );
        let uv = [
            "cpython-3.9.20-windows-x86_64-none",
            "cpython-3.13.1-windows-x86_64-none",
            "cpython-3.13.18-windows-x86_64-none",
        ];
        let sorted = super::by_version(uv.iter().map(|name| name.to_string()), "cpython-");
        assert_eq!(
            sorted,
            [
                "cpython-3.13.18-windows-x86_64-none",
                "cpython-3.13.1-windows-x86_64-none",
                "cpython-3.9.20-windows-x86_64-none",
            ]
        );
    }
}
