const SECRET_KEYS = new Set(["api_key", "apikey", "token", "password", "authorization", "credential", "secret"]);
export function redactSecrets(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactSecrets);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [
      key,
      SECRET_KEYS.has(key.toLowerCase().replaceAll("-", "_")) ? "[redacted]" : redactSecrets(item),
    ]));
  }
  if (typeof value !== "string") return value;
  return value
    .replace(/(api[_-]?key|token|password|authorization)\s*[:=]\s*Bearer\s+[^\s,;]+/gi, "$1=[redacted]")
    .replace(/(api[_-]?key|token|password|authorization)\s*[:=]\s*("[^"]*"|'[^']*'|[^\s,;]+)/gi, "$1=[redacted]")
    .replace(/\b(?:sk-[A-Za-z0-9_-]{6,}|Bearer\s+[A-Za-z0-9._~+/-]+=*)\b/gi, "[redacted]");
}
