# Pet character artwork

Drop a transparent-background portrait PNG here named **`character.png`**
and the pet renderer picks it up automatically on next load
(`Live2DCanvas` → `startCanvas2D` → `loadSpriteImage('/assets/pet/character.png')`).

- If `character.png` is present → it's drawn via `drawSpriteCharacter`
  (fitted, centred, with float + breath motion).
- If absent / 404 → falls back to the procedural chibi placeholder in
  `src/components/petCharacter.ts` (`drawProceduralCharacter`).

## Requirements for the portrait

- **Transparent background** (PNG with alpha) — the pet floats over the desktop.
- Portrait orientation, character roughly centred.
- License must be CC0 / MIT / your own original work — **no Live2D sample
  models, no copyrighted character art**. This keeps the zero-copyright
  invariant the whole rewrite was built for.

## Generating one

If a `SEGMIND_API_KEY` is configured, the `geek-seedream-imagegen` skill can
generate an anime portrait. Otherwise supply your own PNG.
