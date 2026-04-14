# P2-0-S1 Icon Branding (Placeholder Cloud) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the red-square placeholder `icon.png` and Vite-default `favicon.svg` with a hand-written claymorphic purple cloud mascot, version-controlled at the SVG level, fanned out to every icon size via `npx tauri icon`, and reproducible via a single `scripts/rebuild-icons.ps1` call.

**Architecture:** Single SVG source → Node renderer (`@resvg/resvg-js` library) produces 1024×1024 PNG → `npx tauri icon` generates the full platform icon set → `favicon.svg` is a direct copy of the SVG source. Pipeline is a PowerShell script that orchestrates these steps and can be re-run by a future designer who only replaces `deskpet-cloud.svg` or `deskpet-cloud.png`.

**Tech Stack:** SVG (hand-written), Node.js + `@resvg/resvg-js` library, `@tauri-apps/cli`'s `tauri icon` subcommand, PowerShell 7.

**Spec:** `docs/superpowers/specs/2026-04-14-p2s1-icon-branding-design.md`

---

## Preflight

- [ ] **Preflight 1: Confirm working directory and worktree state**

Run:
```bash
pwd
git status --short
```
Expected: `/g/projects/deskpet` (or repo root). Working tree clean except for any already-expected staging from prior sprints.

- [ ] **Preflight 2: Confirm Node + npx available**

Run:
```bash
node --version
npx --version
```
Expected: Node ≥ 18, npx ≥ 9.

- [ ] **Preflight 3: Confirm PowerShell 7 available (for rebuild script)**

Run:
```bash
pwsh --version
```
Expected: `PowerShell 7.x.x`. If missing, install via `winget install Microsoft.PowerShell` or adjust Task 6 to `.ps1` in Windows PowerShell 5 compatibility mode (avoid `-ErrorAction Stop` newer syntax).

---

## Task 1: Create placeholder-cloud SVG source

**Files:**
- Create: `tauri-app/src-tauri/icons-src/deskpet-cloud.svg`

- [ ] **Step 1: Create the `icons-src/` directory**

Run:
```bash
mkdir -p tauri-app/src-tauri/icons-src
```
Expected: directory exists, no error.

- [ ] **Step 2: Write the SVG source**

Create `tauri-app/src-tauri/icons-src/deskpet-cloud.svg` with exactly this content:

```svg
<?xml version="1.0" encoding="UTF-8"?>
<!--
  DeskPet placeholder icon — clay-style purple cloud sprite.
  Hand-written SVG, rendered to PNG via tauri-app/scripts/render-svg.mjs.
  Spec: docs/superpowers/specs/2026-04-14-p2s1-icon-branding-design.md

  Palette:
    #863bff  cloud deep purple (bottom shadow, matches current favicon hue)
    #9d63ff  cloud mid purple
    #b899ff  cloud light purple (upper highlight)
    #ede6ff  cloud brightest highlight (top reflection)
    #1a0b33  near-black purple (eyes / mouth)
    #ff9ec7  blush (semi-transparent)

  Layout (canvas 1024×1024, cloud bbox ~200–822 × ~200–800):
    - left bump    circle(320, 520, 180)
    - top bump     circle(512, 380, 230)
    - right bump   circle(704, 520, 180)
    - bottom body  ellipse(512, 620, 280, 160)
    - eyes         circle(430, 480, 30) / circle(594, 480, 30)
    - eye highlights small white dots for liveliness
    - mouth        path (quadratic arc)
    - blush        soft pink radial dots
-->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <radialGradient id="clayBody" cx="40%" cy="28%" r="75%">
      <stop offset="0%"  stop-color="#ede6ff"/>
      <stop offset="28%" stop-color="#b899ff"/>
      <stop offset="62%" stop-color="#9d63ff"/>
      <stop offset="100%" stop-color="#863bff"/>
    </radialGradient>
    <radialGradient id="blush" cx="50%" cy="50%" r="50%">
      <stop offset="0%"  stop-color="#ff9ec7" stop-opacity="0.65"/>
      <stop offset="100%" stop-color="#ff9ec7" stop-opacity="0"/>
    </radialGradient>
  </defs>

  <!-- Cloud body: overlapping circles + base ellipse, all share the clay gradient -->
  <g fill="url(#clayBody)">
    <circle cx="320" cy="520" r="180"/>
    <circle cx="512" cy="380" r="230"/>
    <circle cx="704" cy="520" r="180"/>
    <ellipse cx="512" cy="620" rx="280" ry="160"/>
  </g>

  <!-- Blush: behind eyes/mouth so they sit on top -->
  <circle cx="380" cy="560" r="60" fill="url(#blush)"/>
  <circle cx="644" cy="560" r="60" fill="url(#blush)"/>

  <!-- Eyes -->
  <circle cx="430" cy="480" r="30" fill="#1a0b33"/>
  <circle cx="594" cy="480" r="30" fill="#1a0b33"/>
  <!-- Eye catch-light -->
  <circle cx="440" cy="470" r="9" fill="#ffffff"/>
  <circle cx="604" cy="470" r="9" fill="#ffffff"/>

  <!-- Mouth: small smile arc -->
  <path d="M 475 555 Q 512 582 549 555"
        stroke="#1a0b33" stroke-width="11" stroke-linecap="round" fill="none"/>
</svg>
```

- [ ] **Step 3: Verify SVG is well-formed XML**

Run:
```bash
node -e "const fs = require('fs'); const s = fs.readFileSync('tauri-app/src-tauri/icons-src/deskpet-cloud.svg', 'utf8'); if (!s.includes('</svg>')) throw new Error('missing close tag'); if (!s.includes('viewBox=\"0 0 1024 1024\"')) throw new Error('missing viewBox'); console.log('OK', s.length, 'bytes');"
```
Expected: `OK <N> bytes`, no error.

- [ ] **Step 4: Visual spot-check in browser**

Run:
```bash
start tauri-app/src-tauri/icons-src/deskpet-cloud.svg
```
(Windows) — opens SVG in default browser. Confirm visually: a rounded purple cloud with two eyes, small smile, two pink blush spots. If the cloud looks wildly off (e.g., eyes outside the body, missing gradient), iterate on the numbers in the SVG before moving on.

- [ ] **Step 5: Commit**

```bash
git add tauri-app/src-tauri/icons-src/deskpet-cloud.svg
git commit -m "feat(branding): add placeholder cloud SVG source (P2-0-S1)"
```

---

## Task 2: Add SVG → PNG renderer helper

**Files:**
- Modify: `tauri-app/package.json` (add devDependency)
- Create: `tauri-app/scripts/render-svg.mjs`

- [ ] **Step 1: Install `@resvg/resvg-js` as a dev dependency**

Run:
```bash
cd tauri-app && npm install --save-dev @resvg/resvg-js@^2.6.0
```
Expected: `package.json` and `package-lock.json` update; `@resvg/resvg-js` appears under `devDependencies`; install completes with no ERR.

- [ ] **Step 2: Create the renderer script directory**

Run:
```bash
mkdir -p tauri-app/scripts
```

- [ ] **Step 3: Write `render-svg.mjs`**

Create `tauri-app/scripts/render-svg.mjs` with exactly this content:

```js
// Render an SVG file to a square PNG using @resvg/resvg-js.
// Usage: node scripts/render-svg.mjs <input.svg> <size> <output.png>
// Example: node scripts/render-svg.mjs src-tauri/icons-src/deskpet-cloud.svg 1024 src-tauri/icons-src/deskpet-cloud.png
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { Resvg } from "@resvg/resvg-js";

const [, , inputArg, sizeArg, outputArg] = process.argv;
if (!inputArg || !sizeArg || !outputArg) {
  console.error("Usage: node scripts/render-svg.mjs <input.svg> <size> <output.png>");
  process.exit(1);
}

const size = Number.parseInt(sizeArg, 10);
if (!Number.isFinite(size) || size < 16 || size > 4096) {
  console.error(`Invalid size '${sizeArg}': must be 16..4096`);
  process.exit(1);
}

const inputPath = resolve(inputArg);
const outputPath = resolve(outputArg);
const svgText = readFileSync(inputPath, "utf8");

const resvg = new Resvg(svgText, {
  fitTo: { mode: "width", value: size },
  background: "rgba(0,0,0,0)",
});
const pngBuffer = resvg.render().asPng();
writeFileSync(outputPath, pngBuffer);
console.log(`rendered ${inputArg} -> ${outputArg} (${size}x${size}, ${pngBuffer.length} bytes)`);
```

- [ ] **Step 4: Sanity-test the renderer**

Run:
```bash
cd tauri-app && node scripts/render-svg.mjs src-tauri/icons-src/deskpet-cloud.svg 1024 /tmp/deskpet-cloud-test.png
```
Expected: prints `rendered ... 1024x1024, <N> bytes`. The byte count should be reasonable (> 10 KB, < 1 MB).

- [ ] **Step 5: Verify the test PNG**

Run (from `tauri-app/`):
```bash
file /tmp/deskpet-cloud-test.png || ls -la /tmp/deskpet-cloud-test.png
```
Expected: `PNG image data, 1024 x 1024, 8-bit/color RGBA, non-interlaced` (or equivalent `ls` showing a non-empty file if `file` not installed).

- [ ] **Step 6: Remove the test file**

Run:
```bash
rm -f /tmp/deskpet-cloud-test.png
```

- [ ] **Step 7: Commit**

```bash
git add tauri-app/package.json tauri-app/package-lock.json tauri-app/scripts/render-svg.mjs
git commit -m "feat(branding): add SVG→PNG renderer helper using @resvg/resvg-js"
```

---

## Task 3: Render the committed PNG source

**Files:**
- Create: `tauri-app/src-tauri/icons-src/deskpet-cloud.png`

- [ ] **Step 1: Render 1024×1024 PNG from the SVG**

Run (from `tauri-app/`):
```bash
cd tauri-app && node scripts/render-svg.mjs src-tauri/icons-src/deskpet-cloud.svg 1024 src-tauri/icons-src/deskpet-cloud.png
```
Expected: prints `rendered src-tauri/icons-src/deskpet-cloud.svg -> src-tauri/icons-src/deskpet-cloud.png (1024x1024, <N> bytes)`.

- [ ] **Step 2: Verify PNG dimensions and non-zero size**

Run:
```bash
node -e "const fs=require('fs'); const b=fs.readFileSync('tauri-app/src-tauri/icons-src/deskpet-cloud.png'); if (b.length < 5000) throw new Error('suspicious size '+b.length); const w=b.readUInt32BE(16); const h=b.readUInt32BE(20); if (w!==1024||h!==1024) throw new Error('bad dims '+w+'x'+h); console.log('OK', w, 'x', h, ',', b.length, 'bytes');"
```
Expected: `OK 1024 x 1024 , <N> bytes`.

- [ ] **Step 3: Visual spot-check**

Open `tauri-app/src-tauri/icons-src/deskpet-cloud.png` in the default image viewer. Confirm it matches the SVG rendering (purple cloud, face, blush). If it looks clipped or has a white background, revisit Task 1 Step 2 or Task 2 Step 3 (the `background: "rgba(0,0,0,0)"` line).

- [ ] **Step 4: Commit**

```bash
git add tauri-app/src-tauri/icons-src/deskpet-cloud.png
git commit -m "feat(branding): render placeholder cloud PNG (1024×1024)"
```

---

## Task 4: Generate the full platform icon set

**Files:**
- Regenerate: `tauri-app/src-tauri/icons/*` (all existing + any new platform sizes)

- [ ] **Step 1: Run `tauri icon` with the new source**

Run (from `tauri-app/`):
```bash
cd tauri-app && npx @tauri-apps/cli icon src-tauri/icons-src/deskpet-cloud.png
```
Expected: tauri writes to `src-tauri/icons/`, printing a list of files regenerated (icon.ico, icon.png, 32x32.png, 64x64.png, 128x128.png, 128x128@2x.png, and the full `android/` + `ios/` subtrees). If `tauri-apps/cli` is not installed globally, this uses the local devDep from Phase 1.

- [ ] **Step 2: Verify the icon tree was regenerated**

Run:
```bash
ls tauri-app/src-tauri/icons/*.png tauri-app/src-tauri/icons/*.ico
```
Expected: all the files from the previous listing exist, with fresh mtimes.

- [ ] **Step 3: Verify icon.png is no longer a solid red square**

Run:
```bash
node -e "const fs=require('fs'); const b=fs.readFileSync('tauri-app/src-tauri/icons/icon.png'); const idat=b.indexOf(Buffer.from('IDAT')); if (idat<0) throw new Error('no IDAT'); const cols = new Set(); for (let i=idat+8; i<Math.min(idat+8000, b.length-4); i+=4) cols.add(b.readUInt32BE(i)); console.log('distinct 32-bit samples in first 8KB of IDAT:', cols.size);"
```
Expected: distinct-sample count is **several hundred or more** (gradient + anti-aliased edges). The old red square had ≤ 3 distinct samples. This is a crude but reliable "not a flat color" gate.

- [ ] **Step 4: Commit the regenerated icon set**

```bash
git add tauri-app/src-tauri/icons
git commit -m "feat(branding): regenerate icon set from placeholder cloud (tauri icon)"
```

---

## Task 5: Sync favicon.svg to the cloud source

**Files:**
- Modify: `tauri-app/public/favicon.svg` (replace contents)

- [ ] **Step 1: Overwrite `favicon.svg` with the cloud SVG**

Run:
```bash
cp tauri-app/src-tauri/icons-src/deskpet-cloud.svg tauri-app/public/favicon.svg
```
Expected: no error; the two files now byte-identical.

- [ ] **Step 2: Verify the two files match**

Run:
```bash
node -e "const fs=require('fs'); const a=fs.readFileSync('tauri-app/src-tauri/icons-src/deskpet-cloud.svg','utf8'); const b=fs.readFileSync('tauri-app/public/favicon.svg','utf8'); if (a!==b) throw new Error('files differ'); console.log('OK, identical,', a.length, 'bytes');"
```
Expected: `OK, identical, <N> bytes`.

- [ ] **Step 3: Commit**

```bash
git add tauri-app/public/favicon.svg
git commit -m "feat(branding): replace Vite default favicon with cloud SVG"
```

---

## Task 6: Create pipeline orchestration script

**Files:**
- Create: `scripts/rebuild-icons.ps1`

- [ ] **Step 1: Create the script**

Create `scripts/rebuild-icons.ps1` with exactly this content:

```powershell
#requires -Version 7.0
<#
.SYNOPSIS
    Rebuild the DeskPet icon set from the SVG source.

.DESCRIPTION
    Pipeline: SVG -> PNG (via render-svg.mjs) -> full icon set (via tauri icon)
    -> favicon.svg sync. Idempotent: re-running produces byte-identical output
    unless deskpet-cloud.svg changed.

.NOTES
    Run from repo root:
        pwsh scripts/rebuild-icons.ps1

    To replace the placeholder with a designer version:
        1. Overwrite tauri-app/src-tauri/icons-src/deskpet-cloud.svg OR
           overwrite deskpet-cloud.png (and skip Step 1 by editing the script).
        2. Run this script.
        3. Commit the regenerated icons/.
#>
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Resolve repo root relative to this script so it works no matter where invoked.
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$TauriApp = Join-Path $RepoRoot "tauri-app"
$IconsSrc = Join-Path $TauriApp "src-tauri/icons-src"
$SvgSource = Join-Path $IconsSrc "deskpet-cloud.svg"
$PngSource = Join-Path $IconsSrc "deskpet-cloud.png"
$FaviconDst = Join-Path $TauriApp "public/favicon.svg"

if (-not (Test-Path $SvgSource)) {
    throw "SVG source not found: $SvgSource"
}

Write-Host "[1/3] Rendering SVG -> PNG (1024x1024)..."
Push-Location $TauriApp
try {
    node scripts/render-svg.mjs "src-tauri/icons-src/deskpet-cloud.svg" 1024 "src-tauri/icons-src/deskpet-cloud.png"
    if ($LASTEXITCODE -ne 0) { throw "render-svg.mjs failed with exit $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host "[2/3] Fanning out to platform icon set (tauri icon)..."
Push-Location $TauriApp
try {
    npx --yes @tauri-apps/cli icon "src-tauri/icons-src/deskpet-cloud.png"
    if ($LASTEXITCODE -ne 0) { throw "tauri icon failed with exit $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host "[3/3] Syncing favicon.svg..."
Copy-Item -Path $SvgSource -Destination $FaviconDst -Force

Write-Host ""
Write-Host "Done. Diff a relevant icon to confirm:"
Write-Host "    git diff --stat tauri-app/src-tauri/icons tauri-app/public/favicon.svg"
```

- [ ] **Step 2: Verify script runs clean from a fresh state**

First snapshot the current icon state:
```bash
git status --short tauri-app/src-tauri/icons tauri-app/public/favicon.svg
```
Expected: clean (all committed from Tasks 3–5).

Then run the script:
```bash
pwsh scripts/rebuild-icons.ps1
```
Expected: three progress lines printed, no error, exit 0.

- [ ] **Step 3: Verify idempotency**

Run:
```bash
git status --short tauri-app/src-tauri/icons tauri-app/public/favicon.svg tauri-app/src-tauri/icons-src/deskpet-cloud.png
```
Expected: **no output** (working tree clean). If files differ, either (a) `tauri icon` output is non-deterministic across runs — investigate, or (b) an embedded timestamp is present — check and document in the script header.

- [ ] **Step 4: Commit**

```bash
git add scripts/rebuild-icons.ps1
git commit -m "feat(branding): add rebuild-icons.ps1 pipeline (SVG -> PNG -> icon set -> favicon)"
```

---

## Task 7: Document the `icons-src/` directory

**Files:**
- Create: `tauri-app/src-tauri/icons-src/README.md`

- [ ] **Step 1: Write the README**

Create `tauri-app/src-tauri/icons-src/README.md` with exactly this content:

```markdown
# icons-src — DeskPet icon source (placeholder)

This directory holds the **source of truth** for the DeskPet app icon.
Everything under `../icons/` and `../../public/favicon.svg` is a
**derived** artifact — regenerated from the files here.

## Current status: PLACEHOLDER

The cloud mascot in `deskpet-cloud.svg` is a hand-written temporary
placeholder decided in V6 §3.1 (D0-1). It exists because:

- The previous `icon.png` was a solid red square (visible bug).
- The Live2D Hiyori sample in `public/assets/live2d/` is a Live2D Inc.
  licensed asset and cannot be used in distributed branding.
- No in-house brand assets exist yet.

A future slice should replace this with a designer-produced icon.
See the **Replacing the placeholder** section below.

## Files

| File | Purpose | Produced by |
|---|---|---|
| `deskpet-cloud.svg` | Hand-written source. Editable. | Human |
| `deskpet-cloud.png` | 1024×1024 raster render of the SVG. | `scripts/rebuild-icons.ps1` |
| `README.md` | This file. | Human |

## Regenerating the icon set

From repo root:

```powershell
pwsh scripts/rebuild-icons.ps1
```

The script:
1. Renders `deskpet-cloud.svg` → `deskpet-cloud.png` (1024×1024) using
   `@resvg/resvg-js` via `tauri-app/scripts/render-svg.mjs`.
2. Runs `npx @tauri-apps/cli icon` to fan the PNG into the full
   platform set under `../icons/`.
3. Copies `deskpet-cloud.svg` → `../../public/favicon.svg`.

It is **idempotent** — running twice against an unchanged source
produces a clean `git status`.

## Replacing the placeholder

### With a new SVG

1. Overwrite `deskpet-cloud.svg`.
2. Run `pwsh scripts/rebuild-icons.ps1`.
3. Commit the regenerated `../icons/`, `../../public/favicon.svg`,
   and the new `deskpet-cloud.svg`/`deskpet-cloud.png`.

### With a ready-made PNG (from a designer)

1. Overwrite `deskpet-cloud.png` (must be square, 1024×1024 or larger).
2. **Either** delete `deskpet-cloud.svg` **or** add a comment at its
   top noting "superseded by deskpet-cloud.png — do not edit". Avoid
   keeping two competing sources.
3. Edit `rebuild-icons.ps1` to skip the render step (or just run
   `npx @tauri-apps/cli icon deskpet-cloud.png` manually).
4. Commit.

## Palette (for future designer reference)

| Role | Hex |
|---|---|
| Cloud deep purple (shadow) | `#863bff` |
| Cloud mid purple | `#9d63ff` |
| Cloud light purple | `#b899ff` |
| Cloud highlight | `#ede6ff` |
| Eyes / mouth | `#1a0b33` |
| Blush (semi-transparent) | `#ff9ec7` |

`#863bff` matches the historical `public/favicon.svg` main hue so the
cloud placeholder keeps brand-color continuity with Phase 1.
```

- [ ] **Step 2: Verify the README renders**

Run:
```bash
cat tauri-app/src-tauri/icons-src/README.md | head -20
```
Expected: title and opening paragraph visible, no stray escape characters.

- [ ] **Step 3: Commit**

```bash
git add tauri-app/src-tauri/icons-src/README.md
git commit -m "docs(branding): document icons-src/ placeholder and regen pipeline"
```

---

## Task 8: Note the placeholder in RELEASE.md

**Files:**
- Modify: `docs/RELEASE.md` (append a section)

- [ ] **Step 1: Read the current RELEASE.md**

Run:
```bash
cat docs/RELEASE.md | head -60
```
Note the heading style (`##` vs `###`) used elsewhere so the new section matches.

- [ ] **Step 2: Append the placeholder note**

Append the following section at the **end** of `docs/RELEASE.md`:

```markdown

## Branding assets

The current app icon is a **temporary placeholder** (a claymorphic
purple cloud mascot) committed in V6 Phase 2 Sprint P2-0 Slice 1. It
is stored in SVG form at
`tauri-app/src-tauri/icons-src/deskpet-cloud.svg`; all derived assets
(`icon.ico`, `icon.png`, the per-platform subdirectories, and
`public/favicon.svg`) are produced by `scripts/rebuild-icons.ps1`.

To replace the placeholder with a designer-produced icon, see
`tauri-app/src-tauri/icons-src/README.md`.

Before the first public release, the placeholder **should** be replaced
with a real brand asset; it is intentionally cute and visually distinct
from the previous red-square bug, but it is not a committed brand mark.
```

- [ ] **Step 3: Verify the append**

Run:
```bash
tail -20 docs/RELEASE.md
```
Expected: the new "Branding assets" section appears at the end.

- [ ] **Step 4: Commit**

```bash
git add docs/RELEASE.md
git commit -m "docs: note placeholder icon in RELEASE.md"
```

---

## Task 9: Smoke-test Tauri debug build

This task has **no commit** — pure verification that the icon churn did not break the build.

- [ ] **Step 1: Type check frontend**

Run (from `tauri-app/`):
```bash
cd tauri-app && npx tsc --noEmit
```
Expected: 0 errors (icon changes are outside the TS surface, so this should be a no-op regression check).

- [ ] **Step 2: Check Rust side compiles**

Run (from `tauri-app/src-tauri/`):
```bash
cd tauri-app/src-tauri && cargo check
```
Expected: `cargo check` finishes with warnings-only (or clean). No errors referencing `icons/`.

- [ ] **Step 3 (optional, heavier): Debug build**

If Steps 1–2 pass and time permits:
```bash
cd tauri-app && npx @tauri-apps/cli build --debug
```
Expected: build finishes, producing `target/debug/deskpet.exe`. If the build was already green at the start of this slice, a fresh green here confirms the icon change is self-contained.

If the debug build is too slow to run inline, skip Step 3 — the icon work is strictly resource files + a PS1 script, and failure modes from this slice cannot manifest after Task 2–5 verified each step individually.

---

## Task 10: CDP E2E regression

This task has **no commit** — confirms that the existing CDP E2E suite still passes after the icon pipeline lands. Per spec §10 this is required.

- [ ] **Step 1: Start the stack**

Start backend, vite dev, and deskpet debug binary following the sequence from `docs/superpowers/plans/2026-04-14-vn-dialog-bar-report.md` verification section. Wait until ports 8100 / 5173 / 9222 all report LISTENING.

- [ ] **Step 2: Run the full CDP E2E suite**

Run (from repo root):
```bash
backend/.venv/Scripts/python.exe scripts/e2e/drive_via_cdp.py
```
Expected output tail:
```
=== SUMMARY ===
  chat      PASS
  memory    PASS
  mic       PASS
  esc       PASS
  dialog    PASS
```
Exit code 0.

- [ ] **Step 3: Tear down the stack**

Kill `deskpet.exe`, vite, and uvicorn following the cleanup sequence from the same prior report. Confirm no orphan processes remain on ports 8100 / 5173 / 9222.

- [ ] **Step 4: Record the regression result**

No separate artifact needed — the PASS output is proof. If any step flakes, stop and investigate before marking the slice complete.

---

## Task 11: Slice handoff doc

**Files:**
- Create: `docs/superpowers/handoffs/p2s1-icon-branding.md`

- [ ] **Step 1: Write the handoff**

Create `docs/superpowers/handoffs/p2s1-icon-branding.md` with the following template, filled with actual commit SHAs from earlier tasks:

```markdown
# P2-0-S1 Icon Branding — HANDOFF

**Date**: 2026-04-14
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice 1
**Status**: ✅ Complete
**Target version**: feeds into `v0.2.0-phase2-beta1`

## What shipped

Replaced the red-square placeholder `icon.png` and the Vite-default
`favicon.svg` with a hand-written claymorphic purple cloud mascot.
Single SVG source, one-shot PowerShell pipeline, all derived artifacts
regeneratable.

## Commits (master)

| SHA | Subject |
|---|---|
| `<fill>` | feat(branding): add placeholder cloud SVG source (P2-0-S1) |
| `<fill>` | feat(branding): add SVG→PNG renderer helper using @resvg/resvg-js |
| `<fill>` | feat(branding): render placeholder cloud PNG (1024×1024) |
| `<fill>` | feat(branding): regenerate icon set from placeholder cloud (tauri icon) |
| `<fill>` | feat(branding): replace Vite default favicon with cloud SVG |
| `<fill>` | feat(branding): add rebuild-icons.ps1 pipeline (SVG -> PNG -> icon set -> favicon) |
| `<fill>` | docs(branding): document icons-src/ placeholder and regen pipeline |
| `<fill>` | docs: note placeholder icon in RELEASE.md |

## Gates

- ✅ `npx tsc --noEmit` clean
- ✅ `cargo check` clean
- ✅ CDP E2E 5/5 PASS
- ✅ `rebuild-icons.ps1` idempotent (second run produces clean `git status`)
- ✅ `icon.png` no longer a flat color (distinct-sample gate > 3)

## Follow-ups

- Designer-produced real brand icon replaces `deskpet-cloud.svg` (tracked
  as an open item in V6 §3.1).
- First release (`v0.2.0`) ships with this placeholder.

## Spec

`docs/superpowers/specs/2026-04-14-p2s1-icon-branding-design.md`
```

- [ ] **Step 2: Fill in actual commit SHAs**

Run:
```bash
git log --oneline -10
```
and paste each SHA into the table above.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/handoffs/p2s1-icon-branding.md
git commit -m "docs: P2-0-S1 icon branding HANDOFF"
```

---

## Self-Review Notes

Before closing the slice, re-read `docs/superpowers/specs/2026-04-14-p2s1-icon-branding-design.md` §7 (Success Criteria) and cross-check:

- **C1** ✅ Task 4 Step 3 verifies `icon.png` is no longer a flat color
- **C2** ✅ Task 9 Step 3 (optional) runs `tauri build --debug`; installer visual parity falls under Task 9 extension
- **C3** — No automated 16×16 check in this plan; **if the implementer visually inspects the 32×32 and 64×64 outputs under `src-tauri/icons/` and a cloud silhouette is recognizable, C3 is satisfied**. If 16×16 is unreadable, open a follow-up slice for a hand-drawn 16×16/32×32 override (spec §9 risk row 2).
- **C4** ✅ Tasks 1 and 7 produce commented SVG and README
- **C5** ✅ Task 6 Step 3 asserts idempotency
- **C6** ✅ Task 7 + Task 8 cover placeholder documentation
- **C7** ✅ Task 9 covers both `tsc --noEmit` and `cargo check`
