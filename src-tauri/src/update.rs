//! Update checks, and the upgrade of a Homebrew install.
//!
//! Homebrew owns `/Applications/differ.app`, so the app never replaces itself. It reads the cask in the tap to
//! learn that a newer version exists, and when Homebrew installed this copy it runs `brew upgrade` for the user:
//! the app quits, a helper script waits for it to go, runs Homebrew and reopens the app.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::AppHandle;

// The cask is what Homebrew installs, so a version read from it is one `brew upgrade` can serve. A release
// on GitHub is published a moment before the cask points at it.
const CASK_URL: &str =
    "https://raw.githubusercontent.com/federicosandrelli/homebrew-tap/main/Casks/differ.rb";
// Fully qualified: a second tap with a `differ` cask makes the bare name ambiguous, and brew then upgrades nothing.
const CASK: &str = "federicosandrelli/tap/differ";
const APP_PATH: &str = "/Applications/differ.app";
const RELEASES: &str = "https://github.com/federicosandrelli/differ/releases";
// How long a check is good for.
const INTERVAL: u64 = 24 * 60 * 60;
// How long to wait after a failed check, so an unreachable tap does not mean a fetch on every launch.
const RETRY: u64 = 60 * 60;
// Homebrew prefixes, Apple silicon first: the cask depends on arm64, so that is where it lands.
const PREFIXES: [&str; 2] = ["/opt/homebrew", "/usr/local"];

/// `update.json` in the cache: the last answer, so a launch is not always a network round trip.
#[derive(Serialize, Deserialize, Default)]
struct Cache {
    checked_at: u64,
    version: String,
    /// When the last attempt failed. Kept beside the last good answer, so being offline keeps what we knew.
    #[serde(default)]
    failed_at: u64,
}

#[derive(Serialize)]
pub struct UpdateStatus {
    /// The running version.
    pub current: String,
    /// The newest version the cask names, once a check has succeeded.
    pub latest: Option<String>,
    /// `latest` is newer than `current`.
    pub newer: bool,
    /// The release page of `latest`, for a copy that did not come from Homebrew.
    pub url: Option<String>,
    /// Homebrew installed this app, so the app can run the upgrade.
    pub managed: bool,
    /// Unix seconds of the last successful check.
    pub checked_at: Option<u64>,
    /// Why this check failed. The last known answer is still returned.
    pub error: Option<String>,
}

/// Whether `then` is less than `limit` seconds ago. A time in the future, from a clock that was set back, is
/// not recent, so the next check is not put off until it passes.
fn within(then: u64, limit: u64) -> bool {
    let now = now();
    then <= now && now - then < limit
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// The leading integers of a dotted version, so 0.1.10 sorts after 0.1.9. Reading stops at the first component
/// that is not all digits, so "0.1.12-beta.1" is 0.1.12 and not a fourth component past the release it precedes.
fn parts(version: &str) -> Vec<u64> {
    let mut out = Vec::new();
    for component in version.trim().trim_start_matches('v').split('.') {
        let digits: String = component.chars().take_while(char::is_ascii_digit).collect();
        out.push(digits.parse().unwrap_or(0));
        if digits.len() != component.len() {
            break;
        }
    }
    out
}

/// Whether `latest` is a later version than `current`. A version without a number is never newer.
pub fn is_newer(latest: &str, current: &str) -> bool {
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
pub fn cask_version(cask: &str) -> Option<String> {
    cask.lines()
        .map(str::trim)
        .find_map(|line| line.strip_prefix("version \""))
        .and_then(|rest| rest.split('"').next())
        .map(str::to_string)
        .filter(|version| !version.is_empty())
}

// Found by path, not through PATH: a GUI app inherits launchd's environment, which has no Homebrew on it.
fn brew_bin() -> Option<PathBuf> {
    PREFIXES
        .iter()
        .map(|prefix| PathBuf::from(prefix).join("bin/brew"))
        .find(|path| path.is_file())
}

fn cask_installed() -> bool {
    PREFIXES
        .iter()
        .any(|prefix| PathBuf::from(prefix).join("Caskroom/differ").is_dir())
}

/// Whether the app may run its own upgrade: Homebrew is here, it has installed the differ cask, and this is the
/// bundle the cask installed. A debug build runs from the cargo target directory, so it skips the last check.
fn managed() -> bool {
    if brew_bin().is_none() || !cask_installed() {
        return false;
    }
    if cfg!(debug_assertions) {
        return true;
    }
    std::env::current_exe()
        .map(|exe| exe.starts_with(APP_PATH))
        .unwrap_or(false)
}

/// The cache the server also uses: `$DIFFER_CACHE`, or `~/.cache/differ`.
fn cache_dir() -> Option<PathBuf> {
    let dir = match std::env::var_os("DIFFER_CACHE") {
        Some(dir) => PathBuf::from(dir),
        None => PathBuf::from(std::env::var_os("HOME")?).join(".cache/differ"),
    };
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

fn read_cache(dir: &Path) -> Option<Cache> {
    let raw = std::fs::read_to_string(dir.join("update.json")).ok()?;
    serde_json::from_str(&raw).ok()
}

fn write_cache(dir: &Path, cache: &Cache) {
    if let Ok(json) = serde_json::to_vec_pretty(cache) {
        let _ = std::fs::write(dir.join("update.json"), json);
    }
}

// curl rather than an HTTP crate: a TLS stack is a large dependency for one request a day, and macOS always has
// /usr/bin/curl. The timestamp makes each check its own cache key, since GitHub serves raw files from a cache.
fn fetch() -> Result<String, String> {
    let url = format!("{CASK_URL}?t={}", now());
    let out = Command::new("/usr/bin/curl")
        .args([
            "-fsSL",
            "--max-time",
            "8",
            "-H",
            "Cache-Control: no-cache",
            &url,
        ])
        .output()
        .map_err(|e| format!("cannot run curl: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "cannot reach {CASK_URL}{}",
            match out.status.code() {
                Some(code) => format!(" (curl exit {code})"),
                None => String::new(),
            }
        ));
    }
    cask_version(&String::from_utf8_lossy(&out.stdout))
        .ok_or_else(|| format!("{CASK_URL} names no version"))
}

fn status_from(current: &str, cache: Option<&Cache>, error: Option<String>) -> UpdateStatus {
    let latest = cache
        .map(|c| c.version.clone())
        .filter(|v| !v.trim().is_empty());
    UpdateStatus {
        newer: latest
            .as_deref()
            .map(|l| is_newer(l, current))
            .unwrap_or(false),
        current: current.to_string(),
        url: latest.as_ref().map(|v| format!("{RELEASES}/tag/v{v}")),
        checked_at: cache.map(|c| c.checked_at).filter(|t| *t > 0),
        latest,
        managed: managed(),
        error,
    }
}

/// Looks for a newer version. Answers from the cache unless the last check is a day old or `force` is set.
/// Async, so the fetch runs off the main thread and the window keeps answering while curl waits.
#[tauri::command(async)]
pub fn update_check(app: AppHandle, force: bool) -> UpdateStatus {
    let current = app.package_info().version.to_string();
    let Some(dir) = cache_dir() else {
        return status_from(&current, None, Some("cannot find the cache".into()));
    };
    let cached = read_cache(&dir);
    let recent = cached
        .as_ref()
        .map(|c| within(c.checked_at, INTERVAL) || within(c.failed_at, RETRY))
        .unwrap_or(false);
    if recent && !force {
        return status_from(&current, cached.as_ref(), None);
    }
    match fetch() {
        Ok(version) => {
            let next = Cache {
                checked_at: now(),
                version,
                failed_at: 0,
            };
            write_cache(&dir, &next);
            status_from(&current, Some(&next), None)
        }
        Err(e) => {
            let mut cache = cached.unwrap_or_default();
            cache.failed_at = now();
            write_cache(&dir, &cache);
            status_from(&current, Some(&cache), Some(e))
        }
    }
}

/// The script that runs the upgrade after the app has quit. It must outlive the app, and it must not live inside
/// the bundle Homebrew is about to replace.
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

/// Quits, lets Homebrew install the new version, and reopens the app. Returns the path of the upgrade log.
#[tauri::command]
pub fn update_run(app: AppHandle) -> Result<String, String> {
    if !managed() {
        return Err("this copy was not installed with Homebrew".into());
    }
    let brew = brew_bin().ok_or("cannot find brew")?;
    let dir = cache_dir().ok_or("cannot find the cache")?;
    let log = crate::log_dir()
        .ok_or("cannot find the log folder")?
        .join("upgrade.log");
    let script = dir.join("upgrade.sh");
    let current = app.package_info().version.to_string();
    std::fs::write(&script, helper_script(&brew, &log, &current))
        .map_err(|e| format!("cannot write {}: {e}", script.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755));
    }
    // Detached, with no inherited stdio, so nothing ties the script to this process once the app exits.
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
    Ok(log.to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::{cask_version, helper_script, is_newer, within};
    use std::path::Path;

    #[test]
    fn compares_numbers_not_text() {
        assert!(is_newer("0.1.10", "0.1.9"));
        assert!(!is_newer("0.1.9", "0.1.10"));
        assert!(is_newer("0.2.0", "0.1.99"));
        assert!(is_newer("1.0.0", "0.99.99"));
    }

    #[test]
    fn same_version_is_not_newer() {
        assert!(!is_newer("0.1.12", "0.1.12"));
        assert!(!is_newer("v0.1.12", "0.1.12"));
        assert!(!is_newer("1.0", "1.0.0"));
        assert!(is_newer("1.0.1", "1.0"));
    }

    #[test]
    fn a_version_without_a_number_is_not_newer() {
        assert!(!is_newer("", "0.1.12"));
        assert!(!is_newer("latest", "0.1.12"));
        assert!(!is_newer("0.0.0", "0.1.12"));
    }

    #[test]
    fn suffixes_are_ignored() {
        assert!(is_newer("0.1.13-beta.1", "0.1.12"));
        assert!(!is_newer("0.1.12-beta.1", "0.1.12"));
    }

    #[test]
    fn a_time_in_the_future_is_not_recent() {
        let now = super::now();
        assert!(within(now - 10, 60));
        assert!(!within(now - 120, 60));
        assert!(!within(now + 3600, 60));
    }

    #[test]
    fn reads_the_version_of_the_cask() {
        let cask = "cask \"differ\" do\n  version \"0.1.39\"\n  sha256 \"abc\"\n\n  url \"https://x/v#{version}/differ_#{version}_aarch64.dmg\"\nend\n";
        assert_eq!(cask_version(cask).as_deref(), Some("0.1.39"));
        assert_eq!(cask_version("cask \"differ\" do\nend\n"), None);
        assert_eq!(cask_version("  version \"\"\n"), None);
    }

    /// The helper runs after the app is gone, so nothing in it may depend on the app, on PATH, or on the bundle
    /// it replaces.
    #[test]
    fn the_helper_stands_on_its_own() {
        let script = helper_script(
            Path::new("/opt/homebrew/bin/brew"),
            Path::new("/Users/x/Library/Logs/differ/upgrade.log"),
            "0.1.39",
        );
        assert!(script
            .contains("\"/opt/homebrew/bin/brew\" upgrade --cask federicosandrelli/tap/differ"));
        assert!(script.contains("PATH=/opt/homebrew/bin:"));
        assert!(script.contains("pgrep -f \"differ.app/Contents/MacOS\""));
        assert!(script.contains("[ $n -lt 60 ]"));
        assert!(script.contains("update --quiet"));
        assert!(script.contains("[ \"$installed\" = \"differ 0.1.39\" ]"));
        let reopen = script
            .find("open -a \"/Applications/differ.app\"")
            .expect("reopens the app");
        assert!(
            !script[..reopen].contains("display alert"),
            "the alert must come after the reopen"
        );
        assert!(script.contains("giving up after 60"));
        assert!(script.contains("/Users/x/Library/Logs/differ/upgrade.log"));
        assert!(script.contains("DEVELOPER_DIR=/Library/Developer/CommandLineTools"));
        assert!(script.contains("item 1 of argv"));
        assert!(script.contains("\"$reason\""));
    }
}
