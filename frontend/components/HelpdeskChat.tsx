"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import type { Interrupt, ResumeEntry } from "@ag-ui/core";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ChatMessage,
  HANDOFF_LABELS,
  PendingApproval,
  Proposal,
  createRunEnvelope,
  extractApproval,
  parseCitationsArgs,
  readSseEvents,
  renderCitedText,
  toAguiMessages,
} from "@/lib/agui";

const WELCOME: ChatMessage = {
  id: "welcome",
  role: "assistant",
  content:
    "Hi! Describe your IT issue, look up an incident (e.g. INC0000057), or ask me to create or update a ticket.",
};

export function HelpdeskChat() {
  const [threadId] = useState(() => crypto.randomUUID());
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME]);
  const [input, setInput] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const messagesRef = useRef(messages);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const autoGrow = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  useEffect(() => {
    autoGrow();
  }, [input, autoGrow]);

  useEffect(() => {
    messagesRef.current = messages;
    scrollRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const updateMessage = useCallback((id: string, patch: Partial<ChatMessage>) => {
    setMessages((current) =>
      current.map((message) => (message.id === id ? { ...message, ...patch } : message)),
    );
  }, []);

  const runAgent = useCallback(
    async (assistantId: string, resume?: ResumeEntry[]) => {
      setIsRunning(true);
      const toolArgs = new Map<string, string>();
      const toolNames = new Map<string, string>();
      const steps: string[] = messagesRef.current.find((message) => message.id === assistantId)?.steps || [];
      let rawText = "";
      let citations = messagesRef.current.find((message) => message.id === assistantId)?.citations || [];
      let firstToken = false;

      try {
        const response = await fetch("/api/copilotkit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            createRunEnvelope({
              threadId,
              messages: toAguiMessages(messagesRef.current),
              resume,
            }),
          ),
        });

        for await (const event of readSseEvents(response)) {
          const type = event.type;

          if (type === "TEXT_MESSAGE_CONTENT") {
            const delta = typeof event.delta === "string" ? event.delta : "";
            if (!firstToken) {
              firstToken = true;
              updateMessage(assistantId, { loading: false, content: "", rawContent: "" });
            }
            rawText += delta;
            updateMessage(assistantId, {
              loading: false,
              rawContent: rawText,
              citations,
              content: renderCitedText(rawText, citations),
            });
          }

          if (type === "TOOL_CALL_START") {
            const toolCallId = typeof event.toolCallId === "string" ? event.toolCallId : "";
            const toolCallName = typeof event.toolCallName === "string" ? event.toolCallName : "";
            if (toolCallId) toolNames.set(toolCallId, toolCallName);
            const label = HANDOFF_LABELS[toolCallName];
            if (label && steps[steps.length - 1] !== label) {
              steps.push(label);
              updateMessage(assistantId, { steps: [...steps] });
            }
          }

          if (type === "TOOL_CALL_ARGS") {
            const toolCallId = typeof event.toolCallId === "string" ? event.toolCallId : "";
            const delta = typeof event.delta === "string" ? event.delta : "";
            if (toolCallId) toolArgs.set(toolCallId, `${toolArgs.get(toolCallId) || ""}${delta}`);
          }

          if (type === "TOOL_CALL_END") {
            const toolCallId = typeof event.toolCallId === "string" ? event.toolCallId : "";
            if (toolNames.get(toolCallId) === "citations") {
              citations = parseCitationsArgs(toolArgs.get(toolCallId));
              updateMessage(assistantId, {
                citations,
                content: renderCitedText(rawText, citations),
                rawContent: rawText,
              });
            }
          }

          if (type === "RUN_FINISHED") {
            const outcome = event.outcome as { type?: string; interrupts?: Interrupt[] } | undefined;
            if (outcome?.type === "interrupt") {
              const approval = outcome.interrupts
                ?.map((interrupt) => extractApproval(interrupt, toolArgs))
                .find((item): item is PendingApproval => Boolean(item));
              if (approval) {
                updateMessage(assistantId, {
                  loading: false,
                  rawContent: rawText,
                  content: renderCitedText(rawText, citations),
                  citations,
                  pendingApproval: approval,
                });
              }
            }
          }

          if (type === "RUN_ERROR") {
            const message = typeof event.message === "string" ? event.message : "The agent reported an error.";
            updateMessage(assistantId, { loading: false, error: true, content: message });
          }
        }
      } catch (error) {
        updateMessage(assistantId, {
          loading: false,
          error: true,
          content: `Error contacting the agent: ${error instanceof Error ? error.message : String(error)}`,
        });
      } finally {
        setIsRunning(false);
      }
    },
    [threadId, updateMessage],
  );

  const submitMessage = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = input.trim();
    if (!text || isRunning) return;

    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: "user", content: text };
    const assistantMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "Thinking…",
      rawContent: "",
      loading: true,
    };

    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    const nextMessages = [...messagesRef.current, userMessage, assistantMessage];
    messagesRef.current = nextMessages;
    setMessages(nextMessages);
    await runAgent(assistantMessage.id);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (input.trim() && !isRunning) {
        event.currentTarget.form?.requestSubmit();
      }
    }
  };

  const respondToApproval = async (messageId: string, approval: PendingApproval, approved: boolean, proposalJson: string) => {
    const updatedMessages = messagesRef.current.map((message) =>
      message.id === messageId
        ? {
            ...message,
            approvalStatus: approved ? ("approved" as const) : ("rejected" as const),
            pendingApproval: undefined,
          }
        : message,
    );

    const assistantMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "Thinking…",
      rawContent: "",
      loading: true,
    };
    const nextMessages = [...updatedMessages, assistantMessage];
    messagesRef.current = nextMessages;
    setMessages(nextMessages);

    await runAgent(assistantMessage.id, [
      {
        interruptId: approval.interruptId,
        status: "resolved",
        payload: {
          approved,
          proposal_json: proposalJson,
        },
      },
    ]);
  };

  return (
    <main className="chat-shell">
      <header className="app-header">ServiceNow IT Helpdesk Agent</header>
      <section className="chat-panel" aria-live="polite">
        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            onApproval={(approved, proposalJson) => {
              if (message.pendingApproval) {
                void respondToApproval(message.id, message.pendingApproval, approved, proposalJson);
              }
            }}
          />
        ))}
        <div ref={scrollRef} />
      </section>
      <form className="composer" onSubmit={submitMessage}>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type your request…  (Enter to send, Shift+Enter for a new line)"
          aria-label="Type your request"
          autoComplete="off"
          rows={1}
          disabled={isRunning}
        />
        <button type="submit" disabled={isRunning || !input.trim()}>
          Send
        </button>
      </form>
    </main>
  );
}

function MessageBubble({
  message,
  onApproval,
}: {
  message: ChatMessage;
  onApproval: (approved: boolean, proposalJson: string) => void;
}) {
  return (
    <article className={`message ${message.role} ${message.loading ? "thinking" : ""} ${message.error ? "error" : ""}`}>
      {message.steps && message.steps.length > 0 ? (
        <ul className="handoff-steps">
          {message.steps.map((step, index) => {
            const isLast = index === message.steps!.length - 1;
            const active = message.loading && isLast;
            return (
              <li key={`${step}-${index}`} className={active ? "active" : "done"}>
                {step}
              </li>
            );
          })}
        </ul>
      ) : null}
      {message.role === "assistant" && !message.loading ? (
        <div className="message-text markdown">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ node, ...props }) => (
                <a {...props} target="_blank" rel="noreferrer noopener" />
              ),
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>
      ) : (
        <div className="message-text">{message.content}</div>
      )}
      {message.pendingApproval ? <ApprovalCard approval={message.pendingApproval} onApproval={onApproval} /> : null}
      {message.approvalStatus ? <div className="approval-status">{message.approvalStatus === "approved" ? "Approved" : "Rejected"}</div> : null}
    </article>
  );
}

function ApprovalCard({
  approval,
  onApproval,
}: {
  approval: PendingApproval;
  onApproval: (approved: boolean, proposalJson: string) => void;
}) {
  const [proposalJson, setProposalJson] = useState(approval.proposalJson);
  const [isEditing, setIsEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = (approved: boolean) => {
    try {
      JSON.parse(proposalJson);
      setError(null);
      onApproval(approved, proposalJson);
    } catch {
      setError("Proposal JSON must be valid before responding.");
    }
  };

  return (
    <div className="approval-card">
      <div className="approval-heading">Human approval required</div>
      <ProposalSummary proposal={approval.proposal} />
      {isEditing ? (
        <label className="proposal-editor">
          Proposal JSON
          <textarea value={proposalJson} onChange={(event) => setProposalJson(event.target.value)} rows={8} />
        </label>
      ) : null}
      {error ? <div className="approval-error">{error}</div> : null}
      <div className="approval-actions">
        <button type="button" className="secondary" onClick={() => setIsEditing((value) => !value)}>
          {isEditing ? "Hide JSON" : "Edit fields"}
        </button>
        <button type="button" className="danger" onClick={() => submit(false)}>
          Reject
        </button>
        <button type="button" onClick={() => submit(true)}>
          Approve
        </button>
      </div>
    </div>
  );
}

function ProposalSummary({ proposal }: { proposal: Proposal }) {
  if (proposal.operation === "update") {
    return (
      <dl className="proposal-grid">
        <Field label="Operation" value="Update incident" />
        <Field label="Incident" value={proposal.incident_number} />
        <Field label="Delta" value={formatDelta(proposal.delta)} />
        <Field label="Summary" value={proposal.summary} />
      </dl>
    );
  }

  return (
    <dl className="proposal-grid">
      <Field label="Operation" value="Create incident" />
      <Field label="Short description" value={proposal.short_description} />
      <Field label="Description" value={proposal.description} />
      <Field label="Assignment group" value={proposal.assignment_group} />
      <Field label="Urgency" value={proposal.urgency} />
    </dl>
  );
}

function Field({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value === undefined || value === null || value === "" ? "—" : String(value)}</dd>
    </div>
  );
}

function formatDelta(delta: Proposal["delta"]): string {
  if (!delta || typeof delta !== "object") return "—";
  return Object.entries(delta)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(", ");
}

