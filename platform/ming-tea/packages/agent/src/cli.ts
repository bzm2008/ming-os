import { createAgentServer } from "./server.js";
const server = createAgentServer({socketPath: process.env.MING_TEA_IPC_PATH, pipeName: process.env.MING_TEA_PIPE_NAME});
await server.listen();
const shutdown = async () => { await server.close(); process.exit(0); };
process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
