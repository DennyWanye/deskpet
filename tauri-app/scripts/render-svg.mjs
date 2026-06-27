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
