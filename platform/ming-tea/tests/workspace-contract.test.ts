import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("workspace contract", () => {
  it("pins the supported Node and pnpm versions", () => {
    const root = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf8"));
    expect(root.engines.node).toBe(">=22.19.0");
    expect(root.packageManager).toBe("pnpm@11.19.0");
    expect(root.workspaces).toEqual(["packages/*", "apps/*"]);
  });
});
