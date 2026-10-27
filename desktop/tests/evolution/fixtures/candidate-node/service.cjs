"use strict";
let boot = null;
let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
  for (;;) {
    const newline = input.indexOf("\n");
    if (newline < 0) break;
    const line = input.slice(0, newline).replace(/\r$/u, "");
    input = input.slice(newline + 1);
    let message;
    try { message = JSON.parse(line); } catch { process.exitCode = 64; process.exit(); }
    if (message.type === "boot" && boot === null) {
      boot = message;
      process.stdout.write(`${JSON.stringify({type:"ready",candidate_id:boot.candidate_id,instance_id:boot.instance_id,boot_nonce:boot.boot_nonce,component_id:boot.component_id})}\n`);
      continue;
    }
    if (message.type === "stop" && boot && message.instance_id === boot.instance_id && message.boot_nonce === boot.boot_nonce) process.exit(0);
  }
});
process.stdin.on("end", () => { if (!boot) process.exitCode = 65; });
