import { mkdir, readFile, appendFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { homedir } from "node:os";
import { randomUUID } from "node:crypto";
import { redactSecrets } from "./redaction.js";

export interface MemoryRecord {
  id: string;
  kind: "profile" | "preference" | "fact" | "note";
  content: string;
  tags: string[];
  createdAt: number;
  source: "user" | "agent" | "session";
}

export interface MemoryConnector {
  recall(query: string, limit: number): Promise<MemoryRecord[]>;
  capture(record: MemoryRecord): Promise<void>;
}

export class LocalMemoryStore implements MemoryConnector {
  private readonly filePath: string;
  constructor(filePath = process.env.MING_TEA_MEMORY_FILE ?? join(homedir(), ".local", "share", "ming-tea", "memory.jsonl")) {
    this.filePath = filePath;
  }

  async capture(record: MemoryRecord): Promise<void> {
    const safe = redactSecrets(record) as MemoryRecord;
    await mkdir(dirname(this.filePath), {recursive: true});
    const existing = await this.readAll();
    if (existing.some((item) => item.content === safe.content && item.kind === safe.kind)) return;
    await appendFile(this.filePath, `${JSON.stringify(safe)}\n`, "utf8");
  }

  async remember(content: string, kind: MemoryRecord["kind"] = "note", tags: string[] = [], source: MemoryRecord["source"] = "user"): Promise<MemoryRecord> {
    const record: MemoryRecord = {id: randomUUID(), kind, content, tags, createdAt: Date.now(), source};
    await this.capture(record);
    return record;
  }

  async recall(query: string, limit = 5): Promise<MemoryRecord[]> {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    return (await this.readAll()).filter((record) => terms.every((term) => `${record.content} ${record.tags.join(" ")}`.toLowerCase().includes(term))).slice(-limit).reverse();
  }

  async profile(): Promise<MemoryRecord[]> {
    return (await this.readAll()).filter((record) => record.kind === "profile" || record.kind === "preference").slice(-20).reverse();
  }

  private async readAll(): Promise<MemoryRecord[]> {
    try {
      const text = await readFile(this.filePath, "utf8");
      return text.split("\n").filter(Boolean).flatMap((line) => {
        try { return [JSON.parse(line) as MemoryRecord]; } catch { return []; }
      });
    } catch { return []; }
  }
}
