use std::path::PathBuf;

fn main() {
    // P3-S3: Inject repo root as a compile-time env var so the resolver's
    // dev fallback (`option_env!("DESKPET_DEV_ROOT")`) works in `cargo run`
    // / `npm run tauri:dev` without requiring users to set env vars. The
    // path is `<manifest>/../..` = tauri-app/src-tauri -> tauri-app -> repo.
    //
    // Release builds also bake this in, but the Bundled priority wins
    // over it at runtime so packaged installs never hit this branch
    // unless the bundled exe is missing AND no env override is set
    // (in which case a NoBackendFound dialog is the correct outcome).
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repo_root = manifest
        .parent()  // tauri-app
        .and_then(|p| p.parent())  // repo root
        .map(|p| p.to_path_buf())
        .unwrap_or(manifest);
    println!("cargo:rustc-env=DESKPET_DEV_ROOT={}", repo_root.display());
    println!("cargo:rerun-if-changed=build.rs");

    tauri_build::build()
}
