import { build } from "esbuild";
import { mkdir } from "node:fs/promises";

await mkdir("apps/desktop/src-tauri/resources", {recursive: true});
await build({
  entryPoints: ["packages/agent/src/cli.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: "apps/desktop/src-tauri/resources/ming-tea-agent.mjs",
  sourcemap: false,
  minify: true,
});
