# Live2D Cubism Core — Third-Party Attribution

DeskPet bundles **Live2D Cubism Core** (`live2dcubismcore.min.js`) as a binary runtime
to render the Live2D model that powers the desktop pet character.

This file is **third-party proprietary software** and is **NOT** covered by DeskPet's
own BUSL-1.1 license. It remains under Live2D Inc.'s separate EULA at all times.

---

## File location in this repository

| Path | Purpose |
|---|---|
| `tauri-app/public/lib/live2dcubismcore.min.js` | Runtime served by the Tauri webview |
| `tauri-app/node_modules/live2dcubismcore/` (gitignored) | npm dependency, pinned to version `1.0.2` |

---

## License

**Live2D Proprietary Software License Agreement**

- Canonical text: https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html
- Japanese version: https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_jp.html
- Copyright © Live2D Inc.

**Important:** the npm package `live2dcubismcore@1.0.2` declares `"license": "ISC"` in
its `package.json`. **This is incorrect npm metadata.** The actual governing license
is the Live2D Proprietary Software License Agreement linked above, not ISC. Do not
rely on the npm field.

---

## What DeskPet relies on (under that EULA)

- **Section 5 (Redistributable Code)** permits bundling `live2dcubismcore.min.js`
  inside a derivative work, provided the derivative work adds "important major
  functions" that use the Redistributable Code. DeskPet's desktop pet UI, motion
  driver, expression system, and lipsync pipeline qualify.
- The Cubism Core binary is **not** relicensed under BUSL-1.1. It remains under the
  Live2D Proprietary Software License Agreement when redistributed as part of DeskPet.

---

## What DeskPet does NOT claim

- DeskPet is **not made by, certified by, or endorsed by Live2D Inc.**
- Live2D® and Cubism® are trademarks of Live2D Inc. Brand guidelines:
  https://www.live2d.jp/brand
- DeskPet does not modify the contents of `live2dcubismcore.min.js`.

---

## Publication License — important notice for downstream users

If you fork DeskPet and publish a commercial product that uses Live2D Cubism, **you**
may be subject to Live2D's "Publication License" requirements. Per Live2D's terms:

- **General Users** (non-commercial individuals) and **Small-Scale Enterprises**
  with annual sales under **10 million JPY** are exempt from the Publication
  License fee, provided they comply with all other terms and notify Live2D if
  sales later exceed the threshold.
- All other commercial publication requires the Publication License Agreement.
  Contact Live2D Customer Support, or see the Simple License Plan:
  https://www.live2d.com/business/SLP

This obligation is **between you (the downstream redistributor) and Live2D Inc.**
DeskPet's BUSL-1.1 license does not relieve you of it.

---

## Updating Cubism Core

When upgrading `live2dcubismcore.min.js`:

1. Download the new build from the official Live2D Cubism SDK release page
2. Verify the license version has not changed (re-read the EULA)
3. Update the version pin in this file
4. Update the version pin in `tauri-app/package.json` if applicable

---

*Attribution prepared: 2026-05-27. Live2D EULA text owned by Live2D Inc.*
