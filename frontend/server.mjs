import { existsSync } from "node:fs";
import { spawn } from "node:child_process";

const port = process.env.PORT || "3000";
const host = process.env.HOSTNAME || "0.0.0.0";
const standaloneServer = ".next/standalone/server.js";

const command = process.execPath;
const args = existsSync(standaloneServer)
  ? [standaloneServer]
  : ["node_modules/next/dist/bin/next", "start", "-H", host, "-p", port];

const child = spawn(command, args, {
  stdio: "inherit",
  env: { ...process.env, PORT: port, HOSTNAME: host },
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code ?? 0);
  }
});
