import { createServer, type Server, type Socket } from "node:net";
import { unlink } from "node:fs/promises";
import { decodeJsonLine, encodeJsonLine } from "./json-lines.js";
import type { Request, Response } from "./index.js";

export type RequestHandler = (request: Request) => Promise<Response>;

export interface JsonLineTransport {
  listen(handler: RequestHandler): Promise<void>;
  close(): Promise<void>;
}

class NetJsonLineTransport implements JsonLineTransport {
  private server?: Server;
  constructor(private readonly path: string) {}

  listen(handler: RequestHandler): Promise<void> {
    this.server = createServer((socket) => this.handle(socket, handler));
    return new Promise((resolve, reject) => {
      this.server!.once("error", reject);
      this.server!.listen(this.path, () => {
        this.server!.off("error", reject);
        resolve();
      });
    });
  }

  private handle(socket: Socket, handler: RequestHandler): void {
    let buffer = "";
    socket.setEncoding("utf8");
    socket.on("data", async (chunk: string) => {
      buffer += chunk;
      while (buffer.includes("\n")) {
        const index = buffer.indexOf("\n");
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        let response: Response;
        try {
          response = await handler(decodeJsonLine(line) as unknown as Request);
        } catch (error) {
          response = {
            id: "unknown",
            ok: false,
            error: {code: "invalid_request", message: error instanceof Error ? error.message : String(error)},
          };
        }
        socket.write(encodeJsonLine(response));
      }
    });
  }

  async close(): Promise<void> {
    if (!this.server) return;
    await new Promise<void>((resolve) => this.server!.close(() => resolve()));
    this.server = undefined;
    if (process.platform !== "win32") await unlink(this.path).catch(() => undefined);
  }
}

export function createUnixTransport(path: string): JsonLineTransport {
  return new NetJsonLineTransport(path);
}

export function createNamedPipeTransport(name: string): JsonLineTransport {
  const path = name.startsWith("\\\\.\\pipe\\") ? name : `\\\\.\\pipe\\${name}`;
  return new NetJsonLineTransport(path);
}
