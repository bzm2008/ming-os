export function encodeJsonLine(value: unknown): string {
  return `${JSON.stringify(value)}\n`;
}

export function decodeJsonLine(line: string): Record<string, unknown> {
  if (!line.trim()) throw new Error("JSON-lines message cannot be blank");
  const value: unknown = JSON.parse(line);
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("JSON-lines message must be an object");
  }
  return value as Record<string, unknown>;
}
