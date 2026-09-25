import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { homedir } from "node:os";

export interface SessionSummary { sessionId: string; scene: string; title: string; summary: string; updatedAt: number; archived: boolean; }

export class SessionLibrary {
  private readonly filePath: string;
  constructor(filePath = process.env.MING_TEA_SESSION_FILE ?? join(homedir(), ".local", "share", "ming-tea", "sessions.json")) { this.filePath = filePath; }

  async upsert(summary: SessionSummary): Promise<void> {
    const sessions = await this.read();
    const next = sessions.filter((item) => item.sessionId !== summary.sessionId);
    next.push(summary);
    await mkdir(dirname(this.filePath), {recursive: true});
    await writeFile(this.filePath, JSON.stringify(next.slice(-500), null, 2), "utf8");
  }

  async search(query = "", limit = 20): Promise<SessionSummary[]> {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    return (await this.read()).filter((item) => terms.every((term) => `${item.title} ${item.summary} ${item.scene}`.toLowerCase().includes(term))).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, limit);
  }

  async recall(sessionIds: string[]): Promise<SessionSummary[]> {
    const ids = new Set(sessionIds);
    return (await this.read()).filter((item) => ids.has(item.sessionId));
  }

  private async read(): Promise<SessionSummary[]> {
    try { return JSON.parse(await readFile(this.filePath, "utf8")) as SessionSummary[]; } catch { return []; }
  }
}
