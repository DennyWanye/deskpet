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
# PowerShell 7
pwsh scripts/rebuild-icons.ps1
# Or Windows PowerShell 5.1 (no PS7 install needed)
powershell -ExecutionPolicy Bypass -File scripts/rebuild-icons.ps1
```

The script:
1. Renders `deskpet-cloud.svg` → `deskpet-cloud.png` (1024×1024) using
   `@resvg/resvg-js` via `tauri-app/scripts/render-svg.mjs`.
2. Runs `npx @tauri-apps/cli icon` to fan the PNG into the full
   platform set under `../icons/`.
3. Copies `deskpet-cloud.svg` → `../../public/favicon.svg`.

**Idempotency:** all PNG / ICO / SVG outputs are byte-identical across
re-runs. The one exception is `icon.icns` (macOS): `tauri icon`'s
`.icns` encoder is non-deterministic. Since DeskPet is Windows-only
through Phase 2, run `git checkout -- ../icons/icon.icns` after a no-op
re-build to keep history clean. Revisit when macOS support lands.

## Replacing the placeholder

### With a new SVG

1. Overwrite `deskpet-cloud.svg`.
2. Run `pwsh scripts/rebuild-icons.ps1` (or the `powershell` variant).
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
