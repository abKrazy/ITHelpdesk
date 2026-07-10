// Next.js `output: "standalone"` produces .next/standalone/server.js with a
// trimmed node_modules, but it does NOT copy .next/static or public/ into the
// standalone tree. The standalone server resolves those assets relative to
// itself (.next/standalone/.next/static and .next/standalone/public), so
// without this copy every /_next/static/* request 404s and the app renders
// unstyled and un-hydrated. Oryx/App Service never does this copy, so we do it
// here as an npm `postbuild` step that runs automatically after `next build`.
import { existsSync, cpSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const standalone = join(root, ".next", "standalone");

if (!existsSync(standalone)) {
  console.log("[copy-standalone-assets] .next/standalone not found — skipping (standalone output disabled).");
  process.exit(0);
}

const copies = [
  { from: join(root, ".next", "static"), to: join(standalone, ".next", "static") },
  { from: join(root, "public"), to: join(standalone, "public") },
];

for (const { from, to } of copies) {
  if (!existsSync(from)) {
    console.log(`[copy-standalone-assets] source missing, skipping: ${from}`);
    continue;
  }
  mkdirSync(dirname(to), { recursive: true });
  cpSync(from, to, { recursive: true });
  console.log(`[copy-standalone-assets] copied ${from} -> ${to}`);
}

console.log("[copy-standalone-assets] done.");
