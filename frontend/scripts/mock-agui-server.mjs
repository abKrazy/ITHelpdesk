import http from "node:http";

const port = Number(process.env.MOCK_AGUI_PORT || 8000);

const server = http.createServer(async (req, res) => {
  if (req.method !== "POST" || !req.url?.startsWith("/agui")) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("not found");
    return;
  }

  let body = "";
  for await (const chunk of req) body += chunk;
  const input = JSON.parse(body || "{}");

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });

  const send = async (event, delay = 15) => {
    res.write(`data: ${JSON.stringify(event)}\n\n`);
    await new Promise((resolve) => setTimeout(resolve, delay));
  };

  const threadId = input.threadId || "mock-thread";
  const runId = input.runId || crypto.randomUUID();
  const latest = [...(input.messages || [])].reverse().find((message) => message.role === "user")?.content || "";

  if (Array.isArray(input.resume) && input.resume.length) {
    const approved = Boolean(input.resume[0]?.payload?.approved);
    await send({ type: "RUN_STARTED", threadId, runId });
    if (approved) {
      await send({
        type: "TOOL_CALL_RESULT",
        messageId: crypto.randomUUID(),
        toolCallId: input.resume[0].interruptId,
        content: `executed via orchestrator: ${input.resume[0]?.payload?.proposal_json || "{}"}`,
        role: "tool",
      });
      const messageId = crypto.randomUUID();
      await send({ type: "TEXT_MESSAGE_START", messageId, role: "assistant" });
      await send({ type: "TEXT_MESSAGE_CONTENT", messageId, delta: "executed" });
      await send({ type: "TEXT_MESSAGE_END", messageId });
    } else {
      const messageId = crypto.randomUUID();
      await send({ type: "TEXT_MESSAGE_START", messageId, role: "assistant" });
      await send({ type: "TEXT_MESSAGE_CONTENT", messageId, delta: "cancelled" });
      await send({ type: "TEXT_MESSAGE_END", messageId });
    }
    await send({ type: "RUN_FINISHED", threadId, runId });
    res.end();
    return;
  }

  await send({ type: "RUN_STARTED", threadId, runId });
  const parentMessageId = crypto.randomUUID();
  await send({ type: "TOOL_CALL_START", toolCallId: "route-1", toolCallName: "route_orchestrator", parentMessageId });
  await send({ type: "TOOL_CALL_END", toolCallId: "route-1" });

  if (/create|update|ticket|incident/i.test(latest)) {
    await send({ type: "TOOL_CALL_START", toolCallId: "incident-1", toolCallName: "manage_servicenow_incident", parentMessageId });
    await send({ type: "TOOL_CALL_END", toolCallId: "incident-1" });
    const messageId = crypto.randomUUID();
    await send({ type: "TEXT_MESSAGE_START", messageId, role: "assistant" });
    await send({ type: "TEXT_MESSAGE_CONTENT", messageId, delta: "I found a ServiceNow write proposal. " });
    const proposalJson = JSON.stringify({
      operation: "create",
      short_description: "Cannot log in",
      description: "User cannot log in after trying the password reset steps.",
      assignment_group: "Identity and Access Management",
      urgency: "2",
    });
    await send({
      type: "TOOL_CALL_START",
      toolCallId: "approval-poc-1",
      toolCallName: "servicenow_write_approval",
      parentMessageId: messageId,
    });
    await send({ type: "TOOL_CALL_ARGS", toolCallId: "approval-poc-1", delta: JSON.stringify({ proposal_json: proposalJson }) });
    await send({ type: "TOOL_CALL_END", toolCallId: "approval-poc-1" });
    await send({
      type: "CUSTOM",
      name: "function_approval_request",
      value: {
        id: "approval-poc-1",
        function_call: { call_id: "approval-poc-1", name: "servicenow_write_approval", arguments: { proposal_json: proposalJson } },
      },
    });
    await send({ type: "TEXT_MESSAGE_END", messageId });
    await send({
      type: "RUN_FINISHED",
      threadId,
      runId,
      outcome: {
        type: "interrupt",
        interrupts: [
          {
            id: "approval-poc-1",
            reason: "tool_call",
            message: "Approve running servicenow_write_approval?",
            toolCallId: "approval-poc-1",
            metadata: {
              agent_framework: {
                function_call: {
                  call_id: "approval-poc-1",
                  name: "servicenow_write_approval",
                  arguments: { proposal_json: proposalJson },
                },
              },
            },
          },
        ],
      },
    });
    res.end();
    return;
  }

  await send({ type: "TOOL_CALL_START", toolCallId: "triage-1", toolCallName: "troubleshoot_from_knowledge_base", parentMessageId });
  await send({ type: "TOOL_CALL_END", toolCallId: "triage-1" });
  const messageId = crypto.randomUUID();
  await send({ type: "TEXT_MESSAGE_START", messageId, role: "assistant" });
  await send({ type: "TEXT_MESSAGE_CONTENT", messageId, delta: "Try resetting your password " });
  await send({ type: "TEXT_MESSAGE_CONTENT", messageId, delta: "from the portal. 【1:1†source】" });
  await send({
    type: "TOOL_CALL_START",
    toolCallId: "citations-1",
    toolCallName: "citations",
    parentMessageId: messageId,
  });
  await send({
    type: "TOOL_CALL_ARGS",
    toolCallId: "citations-1",
    delta: JSON.stringify({
      citations: [
        {
          index: 1,
          sourceId: "password-reset",
          sourceTitle: "Password Reset",
          sourceName: "password-reset.md",
          assignmentGroup: "Identity and Access Management",
          markers: ["【1:1†source】"],
          chunkIds: ["chunk-1"],
          url: "mcp://internal/chunk-1",
        },
      ],
    }),
  });
  await send({ type: "TOOL_CALL_END", toolCallId: "citations-1" });
  await send({ type: "TEXT_MESSAGE_END", messageId });
  await send({ type: "RUN_FINISHED", threadId, runId });
  res.end();
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Mock AG-UI endpoint listening on http://127.0.0.1:${port}/agui`);
});

process.on("SIGINT", () => server.close(() => process.exit(0)));
process.on("SIGTERM", () => server.close(() => process.exit(0)));

