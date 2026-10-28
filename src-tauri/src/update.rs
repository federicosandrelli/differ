//! Update checks, and the upgrade of a Homebrew install.
//!
//! Homebrew owns `/Applications/differ.app`, so the app never replaces itself. It reads the cask in the tap to
//! learn that a newer version exists, and when Homebrew installed this copy it runs `brew upgrade` for the user:
//! the app quits, a helper script waits for it to go, runs Homebrew and reopens the app.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use tauri::AppHandle;

// The cask is what Homebrew installs, so a version read from it is one `brew upgrade` can serve. A release
// on GitHub is published a moment before the cask points at it.
const CASK_URL: &str =
    "https://raw.githubusercontent.com/federicosandrelli/homebrew-tap/main/Casks/differ.rb";
// Fully qualified: a second tap with a `differ` cask makes the bare name ambiguous, and brew then upgrades nothing.
const CASK: &str = "federicosandrelli/tap/differ";
const APP_PATH: &str = "/Applications/differ.app";
const RELEASES: &str = "https://github.com/federicosandrelli/differ/releases";
const INTERVAL: u64 = 12 * 60 * 60;
const PREFIXES: [&str; 2] = ["/opt/homebrew", "/usr/local"];

/// The version the cask named at a successful fetch.
struct Fetch {
    at: u64,
    version: String,
}

static LAST_FETCH: Mutex<Option<Fetch>> = Mutex::new(None);

#[derive(Serialize)]
pub struct UpdateStatus {
    pub current: String,
    /// The version the cask named at the last successful fetch since the app started.
    pub latest: Option<String>,
    pub newer: bool,
    /// The release page of `latest`.
    pub url: Option<String>,
    /// Homebrew installed this copy, so `update_run` can upgrade it.
    pub managed: bool,
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn parts(version: &str) -> Vec<u64> {
    version
        .trim()
        .split('.')
        .map(|component| component.parse().unwrap_or(0))
        .collect()
}

/// Whether `latest` is a later version than `current`. A version without a number is never newer.
fn is_newer(latest: &str, current: &str) -> bool {
    let (a, b) = (parts(latest), parts(current));
    if a.iter().all(|n| *n == 0) {
        return false;
    }
    for i in 0..a.len().max(b.len()) {
        let (x, y) = (
            a.get(i).copied().unwrap_or(0),
            b.get(i).copied().unwrap_or(0),
        );
        if x != y {
            return x > y;
        }
    }
    false
}

/// The version the cask names, from the text of the cask file.
fn cask_version(cask: &str) -> Option<String> {
    cask.lines()
        .map(str::trim)
        .find_map(|line| line.strip_prefix("version \""))
        .and_then(|rest| rest.split('"').next())
        .map(str::to_string)
        .filter(|version| !version.is_empty())
}

fn brew_bin() -> Option<PathBuf> {
    PREFIXES
        .iter()
        .map(|prefix| PathBuf::from(prefix).join("bin/brew"))
        .find(|path| path.is_file())
}

/// Whether Homebrew installed the differ cask, and this is the bundle it installed. A debug build runs from the
/// cargo target directory, so it skips the last check.
fn managed() -> bool {
    let cask_installed = PREFIXES
        .iter()
        .any(|prefix| PathBuf::from(prefix).join("Caskroom/differ").is_dir());
    if brew_bin().is_none() || !cask_installed {
        return false;
    }
    cfg!(debug_assertions)
        || std::env::current_exe()
            .map(|exe| exe.starts_with(APP_PATH))
            .unwrap_or(false)
}

/// The cache folder of `build.py`: `$DIFFER_CACHE`, or `~/.cache/differ`.
fn cache_dir() -> Option<PathBuf> {
    let dir = match std::env::var_os("DIFFER_CACHE") {
        Some(dir) => PathBuf::from(dir),
        None => PathBuf::from(std::env::var_os("HOME")?).join(".cache/differ"),
    };
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

// curl rather than an HTTP crate: a TLS stack is a large dependency for a few requests a day.
fn fetch() -> Option<String> {
    let out = Command::new("/usr/bin/curl")
        .args(["-fsSL", "--max-time", "8", CASK_URL])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    cask_version(&String::from_utf8_lossy(&out.stdout))
}

/// Whether a fetch at `at` is less than 12 hours old. A time in the future, from a clock that was set back, is not.
fn fresh(at: u64) -> bool {
    let now = now();
    at <= now && now - at < INTERVAL
}

/// Whether to fetch the cask: when `force` is set, or when the last fetch is missing or 12 hours old.
fn needs_fetch(force: bool, last: Option<&Fetch>) -> bool {
    force || !last.is_some_and(|f| fresh(f.at))
}

/// Looks for a newer version. Fetches the cask on the first call since the app started, when `force` is set, or
/// when the last successful fetch is 12 hours old. Answers from the last successful fetch since the app started,
/// so until one succeeds, `latest` is `None` and `newer` is false. Calls run one at a time, so a call that
/// starts during a fetch answers from it.
#[tauri::command(async)]
pub fn update_check(app: AppHandle, force: bool) -> UpdateStatus {
    let current = app.package_info().version.to_string();
    let mut last = LAST_FETCH
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if needs_fetch(force, last.as_ref()) {
        if let Some(version) = fetch() {
            *last = Some(Fetch { at: now(), version });
        }
    }
    let latest = last.as_ref().map(|f| f.version.clone());
    drop(last);
    UpdateStatus {
        newer: latest.as_deref().is_some_and(|l| is_newer(l, &current)),
        url: latest.as_ref().map(|v| format!("{RELEASES}/tag/v{v}")),
        current,
        latest,
        managed: managed(),
    }
}

/// The script that runs the upgrade after the app has quit.
fn helper_script(brew: &Path, log: &Path, current: &str) -> String {
    format!(
        r#"#!/bin/sh
# Written by differ. Upgrades the Homebrew cask, then reopens the app. Safe to delete.
PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export PATH
exec >>"{log}" 2>&1
echo "--- $(date) upgrading"

# Homebrew replaces the bundle, which fails while the app still runs. Wait for it to go, but not forever.
n=0
while pgrep -f "differ.app/Contents/MacOS" >/dev/null 2>&1 && [ $n -lt 60 ]; do
  sleep 0.5
  n=$((n + 1))
done

# Homebrew refuses every command until the Xcode licence is accepted, and each Xcode update asks again. The
# check runs xcrun against the selected developer directory; the Command Line Tools carry no licence, so point
# xcrun there when they are present. A cask install needs no compiler.
if [ -x /Library/Developer/CommandLineTools/usr/bin/clang ]; then
  DEVELOPER_DIR=/Library/Developer/CommandLineTools
  export DEVELOPER_DIR
fi

# Where this run starts in the log, so a failure quotes its own reason and not one from an earlier run.
start=$(wc -l <"{log}")

# The tap has to be refreshed first, or brew still sees the old cask. A failed refresh is not fatal on its own:
# the check below catches an upgrade that left the running version in place.
"{brew}" update --quiet || true
"{brew}" upgrade --cask {cask}
status=$?
installed=$("{brew}" list --cask --versions differ 2>/dev/null)
if [ $status -eq 0 ] && [ "$installed" = "differ {current}" ]; then
  echo "Error: differ is still {current}; brew update may have failed."
  status=1
fi

# Reopen first, whatever happened: an alert is modal, and raised before this line it leaves the user with no app.
open -a "{app}"

if [ $status -eq 0 ]; then
  echo "upgraded"
else
  reason=$(tail -n +"$((start + 1))" "{log}" | grep '^Error:' | tail -n 1 | cut -c1-300)
  [ -n "$reason" ] || reason=$(tail -n +"$((start + 1))" "{log}" | tail -n 1 | cut -c1-300)
  [ -n "$reason" ] || reason="Homebrew did not finish."
  echo "upgrade failed ($status): $reason"
  # The reason goes in as an argument, so no quoting to get wrong. Bounded, so a dialog nobody answers cannot
  # keep this script running for the rest of the session.
  osascript \
    -e 'on run argv' \
    -e 'display alert "differ could not update" message (item 1 of argv & return & return & "Run: brew upgrade --cask {cask}") as warning giving up after 60' \
    -e 'end run' \
    "$reason" >/dev/null 2>&1
fi
"#,
        log = log.display(),
        brew = brew.display(),
        app = APP_PATH,
        cask = CASK,
        current = current,
    )
}

/// Quits, lets Homebrew install the new version, and reopens the app. The helper script logs to `upgrade.log`
/// in `~/Library/Logs/differ`.
#[tauri::command]
pub fn update_run(app: AppHandle) -> Result<(), String> {
    if !managed() {
        return Err("this copy was not installed with Homebrew".into());
    }
    let brew = brew_bin().ok_or("cannot find brew")?;
    let dir = cache_dir().ok_or("cannot find the cache")?;
    let log = crate::log_dir()
        .ok_or("cannot find the log folder")?
        .join("upgrade.log");
    // Outside the bundle, which Homebrew replaces while the script runs.
    let script = dir.join("upgrade.sh");
    let current = app.package_info().version.to_string();
    std::fs::write(&script, helper_script(&brew, &log, &current))
        .map_err(|e| format!("cannot write {}: {e}", script.display()))?;
    // No inherited stdio, so the script holds no pipe to this process after the app exits.
    Command::new("/bin/sh")
        .arg(&script)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("cannot start the upgrade: {e}"))?;
    // Long enough for this reply to reach the shell, short enough that the helper is not left waiting.
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(700));
        app.exit(0);
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{cask_version, fresh, helper_script, is_newer, needs_fetch, now, Fetch};
    use std::path::Path;

    #[test]
    fn compares_versions_as_numbers() {
        assert!(is_newer("0.1.10", "0.1.9"));
        assert!(!is_newer("0.1.9", "0.1.10"));
        assert!(is_newer("1.0.1", "1.0"));
        assert!(!is_newer("1.0", "1.0.0"));
    }

    #[test]
    fn a_version_without_a_number_is_not_newer() {
        assert!(!is_newer("", "0.1.12"));
        assert!(!is_newer("latest", "0.1.12"));
    }

    #[test]
    fn a_check_from_the_future_is_not_fresh() {
        let now = now();
        assert!(fresh(now - 10));
        assert!(!fresh(now - 2 * 24 * 60 * 60));
        assert!(!fresh(now + 3600));
    }

    #[test]
    fn fetches_at_first_then_when_the_last_fetch_goes_stale() {
        let recent = Fetch {
            at: now() - 60,
            version: "0.1.59".into(),
        };
        let stale = Fetch {
            at: now() - 13 * 60 * 60,
            version: "0.1.59".into(),
        };
        assert!(needs_fetch(false, None));
        assert!(!needs_fetch(false, Some(&recent)));
        assert!(needs_fetch(true, Some(&recent)));
        assert!(needs_fetch(false, Some(&stale)));
    }

    #[test]
    fn reads_the_version_of_the_cask() {
        let cask = "cask \"differ\" do\n  version \"0.1.39\"\n  sha256 \"abc\"\nend\n";
        assert_eq!(cask_version(cask).as_deref(), Some("0.1.39"));
        assert_eq!(cask_version("cask \"differ\" do\nend\n"), None);
        assert_eq!(cask_version("  version \"\"\n"), None);
    }

    #[test]
    fn the_helper_reopens_the_app_before_any_alert() {
        let script = helper_script(
            Path::new("/opt/homebrew/bin/brew"),
            Path::new("/Users/x/Library/Logs/differ/upgrade.log"),
            "0.1.39",
        );
        assert!(script
            .contains("\"/opt/homebrew/bin/brew\" upgrade --cask federicosandrelli/tap/differ"));
        assert!(script.contains("exec >>\"/Users/x/Library/Logs/differ/upgrade.log\""));
        assert!(script.contains("[ \"$installed\" = \"differ 0.1.39\" ]"));
        let reopen = script
            .find("open -a \"/Applications/differ.app\"")
            .expect("reopens the app");
        assert!(!script[..reopen].contains("display alert"));
    }
}
