# bundle-packaging Specification

## Purpose
TBD - created by archiving change p4-s21-msi-polish-pack. Update Purpose after archive.
## Requirements
### Requirement: PyInstaller bundle MUST ship the unified-schema config.toml
The system SHALL ship `config.toml` (the new unified `[llm]` schema)
inside the PyInstaller frozen bundle at `_MEIPASS/config.toml`. The
spec's `datas` SHALL include `("../config.toml", ".")`. This makes
the migration source available at runtime even on machines that have
no `<exe_dir>/config.toml`.

#### Scenario: Frozen exe finds bundled config.toml
- **GIVEN** the user has no `<install_dir>/config.toml`
- **WHEN** backend starts in frozen mode
- **THEN** `_bundle_default_config_path()` returns `_MEIPASS/config.toml`
- **AND** `seed_user_config_if_missing()` uses it as migration source

### Requirement: Legacy [llm.local]/[llm.cloud] schema MUST auto-migrate
On startup, if the user's `%AppData%/deskpet/config.toml` matches the
legacy schema (top-level `[llm]` table contains `local` or `cloud`
sub-tables), the system SHALL back up the file as
`config.toml.legacy-bak` and overwrite with the bundle's unified
schema. Existing `llm_runtime.json` (the user's actual base_url /
model / api_key) is NOT touched.

#### Scenario: Legacy schema is migrated on startup
- **GIVEN** user has `[llm.cloud] base_url = "sealos..."` in config.toml
- **WHEN** backend starts
- **THEN** `config.toml.legacy-bak` exists with the old contents
- **AND** `config.toml` now matches the bundle's unified schema
- **AND** `llm_runtime.json` is unchanged

#### Scenario: Unified schema is left alone
- **GIVEN** user has flat `[llm] model = "..." base_url = "..."` (no sub-tables)
- **WHEN** backend starts
- **THEN** config.toml mtime is unchanged
- **AND** no `.legacy-bak` is created

### Requirement: MSI build MUST use external cab to keep C: cache footprint small
The MSI build script SHALL patch the generated wxs to use
`<MediaTemplate EmbedCab="no" MaximumUncompressedMediaSize="1900" />`.
This produces an `.msi` file (~10 MB) plus N sibling `.cab` files
(each < 1.9 GB, totaling ~5.4 GB). Windows Installer cache
(`C:\Windows\Installer\`) only stores the small `.msi`, so install
no longer requires ~7 GB free on the system drive.

#### Scenario: Built MSI directory contains .msi + cabs
- **GIVEN** `scripts/build-msi.ps1` runs successfully
- **THEN** `tauri-app/src-tauri/target/release/bundle/msi/` contains
  `DeskPet_*.msi` AND one or more `*.cab` files
- **AND** the `.msi` size is < 100 MB

#### Scenario: Install drops only the .msi into Windows/Installer cache
- **GIVEN** the user installs the MSI
- **WHEN** install completes
- **THEN** `C:\Windows\Installer\` contains only the `.msi`-sized cache entry
- **AND** the cabs are NOT copied to C:

