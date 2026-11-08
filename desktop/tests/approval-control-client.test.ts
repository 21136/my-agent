import assert from "node:assert/strict";
import { test } from "node:test";
import http from "node:http";
import { requestApprovalPreferences, parseApprovalControlBootstrap } from "../electron/approval-control-client.ts";

const token = "a".repeat(64);

test("approval control bootstrap accepts only a bounded loopback endpoint and private token", () => {
  assert.deepEqual(parseApprovalControlBootstrap({ approval_control_available: true, approval_control_port: 43210, approval_control_token: token }), { port: 43210, token });
  assert.equal(parseApprovalControlBootstrap({ approval_control_available: false, approval_control_port: 43210, approval_control_token: token }), null);
  assert.equal(parseApprovalControlBootstrap({ approval_control_available: true, approval_control_port: 43210, approval_control_token: "bad" }), null);
});

test("approval requests send the credential only in the main-process Authorization header", async () => {
  let observed;
  const server = http.createServer((req, res) => {
    let body = "";
    req.setEncoding("utf8"); req.on("data", (part) => body += part);
    req.on("end", () => {
      observed = { url: req.url, headers: req.headers, body };
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ type: "approval_preferences.done", status: "ok", approval_mode: "auto", saved_approval_mode: "auto", available_modes: ["manual", "auto", "full"] }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const port = server.address().port;
    const result = await requestApprovalPreferences({ port, token }, "auto");
    assert.equal(result.approval_mode, "auto");
    assert.equal(observed.headers.authorization, `Bearer ${token}`);
    assert.equal(observed.headers.origin, undefined);
    assert.equal(observed.url, "/approval-preferences");
    assert.equal(observed.body.includes(token), false);
  } finally { server.close(); }
});
