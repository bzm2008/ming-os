// Build the global-hotkey daemon for the requested target (default: host).
//
// Tauri's externalBin convention requires binaries/ming-tea-hotkey-<target>.
// The previous inline script named the copy after the rustc HOST triple, so a
// cross-target build (macos-latest arm64 runner building x86_64-apple-darwin)
// produced aarch64-named binaries that the x86_64 Tauri build could not find,
// failing the first CI run.
//
// Usage: pnpm build:hotkey [-- <target-triple>]
//   no argument  -> host triple (local dev: build for this machine)
//   <triple>     -> cargo build --target <triple>, copy from the
//                   target-specific output directory.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, copyFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const desktopDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const daemonDir = path.resolve(desktopDir, "../hotkey-daemon");
const binariesDir = path.resolve(desktopDir, "src-tauri/binaries");

const argIndex = process.argv.indexOf("--");
const target = argIndex >= 0 ? process.argv[argIndex + 1] : undefined;

function hostTriple() {
  const version = execFileSync("rustc", ["-vV"], { encoding: "utf8" });
  const match = /^host:\s*(\S+)$/m.exec(version);
  if (!match) throw new Error("could not determine rustc host triple");
  return match[1];
}

const triple = target || hostTriple();
const isWindows = process.platform === "win32";
const exe = isWindows ? "ming-tea-hotkey.exe" : "ming-tea-hotkey";

const cargoArgs = ["build", "--release", "--manifest-path", path.join(daemonDir, "Cargo.toml")];
if (target) cargoArgs.push("--target", target);
execFileSync("cargo", cargoArgs, { stdio: "inherit" });

const source = target
  ? path.join(daemonDir, "target", target, "release", exe)
  : path.join(daemonDir, "target", "release", exe);
const destination = path.join(binariesDir, `ming-tea-hotkey-${triple}${isWindows ? ".exe" : ""}`);

if (!existsSync(source)) {
  throw new Error(`hotkey daemon not found after build: ${source}`);
}
mkdirSync(binariesDir, { recursive: true });
copyFileSync(source, destination);
console.log(`hotkey helper: ${destination}`);
