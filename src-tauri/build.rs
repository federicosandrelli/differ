fn main() {
    // The shell loads from the local server, a remote origin to Tauri, which reaches a command of the app only
    // through a permission the capability names. The manifest generates an allow-<command> permission for each
    // command, such as allow-update-check.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "update_check",
            "update_run",
            "server_failed",
            "retry_server",
        ]),
    ))
    .expect("failed to run tauri-build");
}
