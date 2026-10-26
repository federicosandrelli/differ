fn main() {
    // The shell loads from the local server, a remote origin to Tauri, which reaches a command of the app only
    // through a permission the capability names. The manifest generates allow-update-check and allow-update-run.
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&["update_check", "update_run"]),
        ),
    )
    .expect("failed to run tauri-build");
}
