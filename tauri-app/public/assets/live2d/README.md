# Live2D models — bring your own

This open-source repository ships the **Live2D loading/animation code**, but
**not** any Live2D character model files (`.moc3`, textures, motions). Those are
third-party artwork with their own licenses and are not redistributed here.

## How to add a model

1. Obtain a Cubism 4 model you are licensed to use, and unzip it into a
   subdirectory under this folder, e.g.:

   ```
   tauri-app/public/assets/live2d/<your-model>/<your-model>.model3.json
   ```

   Keep the directory/file names free of spaces and special characters for the
   friendliest URLs (the dev server URL-encodes them, but plain names are best).

2. The dev server / build auto-scans this folder and generates
   `/assets/live2d/models.json`, which populates the **桌宠形象 / Pet model**
   dropdown in Settings. Add/remove a model and refresh — no code change needed.

3. To make a model the default, set `DEFAULT_PET_MODEL_ID` in
   [`src/petModels.ts`](../../../src/petModels.ts) to its slug.

If no model is present, the app falls back to a built-in procedural placeholder
character, so it still runs out of the box.

## Licensing note

Most freely-distributed Live2D models are licensed for streaming/video use only
and **do not** grant redistribution inside a software product. Verify each
model's license before bundling it into a distributed build.
