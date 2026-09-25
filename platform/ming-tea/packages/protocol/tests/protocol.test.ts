import { describe, expect, it } from "vitest";
import { decodeJsonLine, encodeJsonLine } from "../src/json-lines.js";

describe("JSON-lines protocol", () => {
  it("round trips a session request as one JSON line", () => {
    const request = {id: "1", action: "session.create", payload: {scene: "office"}};
    expect(decodeJsonLine(encodeJsonLine(request))).toEqual(request);
  });

  it("rejects malformed and non-object messages", () => {
    expect(() => decodeJsonLine("{")).toThrow();
    expect(() => decodeJsonLine("[]")).toThrow();
    expect(() => decodeJsonLine("  ")).toThrow();
  });
});
