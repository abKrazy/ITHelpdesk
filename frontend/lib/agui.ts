import type { Interrupt, Message, ResumeEntry } from "@ag-ui/core";

export type Citation = {
  index?: number;
  sourceId?: string;
  sourceTitle?: string;
  sourceName?: string;
  assignmentGroup?: string;
  markers?: string[];
  chunkIds?: string[];
  url?: string;
};

export type Proposal = {
  operation?: "create" | "update" | string;
  short_description?: string;
  description?: string;
  assignment_group?: string;
  urgency?: string | number;
  incident_number?: string;
  delta?: Record<string, unknown>;
  summary?: string;
  [key: string]: unknown;
};

export type PendingApproval = {
  interruptId: string;
  proposalJson: string;
  proposal: Proposal;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  rawContent?: string;
  citations?: Citation[];
  pendingApproval?: PendingApproval;
  approvalStatus?: "approved" | "rejected";
  loading?: boolean;
  error?: boolean;
  steps?: string[];
};

export const HANDOFF_LABELS: Record<string, string> = {
  route_orchestrator: "Calling Orchestrator",
  troubleshoot_from_knowledge_base: "Calling Triage Agent",
  manage_servicenow_incident: "Calling Incident Agent",
};

export function renderCitedText(rawText: string, citations: Citation[] = []): string {
  const markerToIndex = new Map<string, number>();
  for (const entry of citations) {
    const idx = Number(entry?.index);
    if (!Number.isFinite(idx)) continue;
    for (const marker of entry.markers || []) {
      markerToIndex.set(marker, idx);
    }
  }

  const markerPattern = /【(\d+):(\d+)†[^】]*】/g;
  let rendered = rawText.replace(markerPattern, (marker) => {
    const idx = markerToIndex.get(marker);
    return idx ? `[${idx}]` : "";
  });
  rendered = rendered.replace(/(\[\d+\])(?:\s*\1)+/g, "$1");

  if (!citations.length) return rendered;

  const lines = citations
    .slice()
    .sort((a, b) => Number(a.index || 0) - Number(b.index || 0))
    .map((entry) => {
      const idx = Number(entry.index);
      const title = entry.sourceTitle || entry.sourceName || entry.sourceId || entry.markers?.[0] || "Source";
      const suffix = entry.sourceName && entry.sourceName !== title ? ` — ${entry.sourceName}` : "";
      return `[${idx}] ${title}${suffix}`;
    });

  return `${rendered.trimEnd()}\n\nSources:\n${lines.join("\n")}`;
}

export function toAguiMessages(messages: ChatMessage[]): Message[] {
  return messages
    .filter((message) => !message.loading && !message.error)
    .map((message) => ({
      id: message.id,
      role: message.role,
      content: message.rawContent || message.content,
    })) as Message[];
}

export function createRunEnvelope(params: {
  threadId: string;
  messages: Message[];
  resume?: ResumeEntry[];
}) {
  return {
    method: "agent/run",
    params: { agentId: "helpdesk" },
    body: {
      threadId: params.threadId,
      runId: crypto.randomUUID(),
      state: {},
      messages: params.messages,
      tools: [],
      context: [],
      forwardedProps: {},
      ...(params.resume ? { resume: params.resume } : {}),
    },
  };
}

export async function* readSseEvents(response: Response): AsyncGenerator<Record<string, unknown>> {
  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => "");
    throw new Error(`CopilotKit runtime returned HTTP ${response.status}: ${detail || response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const drain = function* (chunk: string): Generator<Record<string, unknown>> {
    buffer += chunk;
    let boundary: number;
    while ((boundary = buffer.search(/\r?\n\r?\n/)) >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(buffer.match(/\r?\n\r?\n/)?.index === boundary && buffer[boundary] === "\r" ? boundary + 4 : boundary + 2);
      const data = frame
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");
      if (!data || data === "[DONE]") continue;
      try {
        yield JSON.parse(data) as Record<string, unknown>;
      } catch {
        // Ignore malformed SSE frames; the backend may keepalive with comments.
      }
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    yield* drain(decoder.decode(value, { stream: true }));
  }
  yield* drain(decoder.decode());
}

export function extractApproval(interrupt: Interrupt, toolArgs: Map<string, string>): PendingApproval | null {
  const args = interrupt.metadata?.agent_framework?.function_call?.arguments;
  const proposalFromInterrupt = typeof args?.proposal_json === "string" ? args.proposal_json : undefined;
  const proposalFromTool = interrupt.toolCallId ? parseProposalFromToolArgs(toolArgs.get(interrupt.toolCallId)) : undefined;
  const proposalJson = proposalFromInterrupt || proposalFromTool;
  if (!proposalJson) return null;

  try {
    return {
      interruptId: interrupt.id,
      proposalJson,
      proposal: JSON.parse(proposalJson) as Proposal,
    };
  } catch {
    return null;
  }
}

function parseProposalFromToolArgs(rawArgs: string | undefined): string | undefined {
  if (!rawArgs) return undefined;
  try {
    const parsed = JSON.parse(rawArgs) as { proposal_json?: unknown };
    return typeof parsed.proposal_json === "string" ? parsed.proposal_json : undefined;
  } catch {
    return undefined;
  }
}

export function parseCitationsArgs(rawArgs: string | undefined): Citation[] {
  if (!rawArgs) return [];
  try {
    const parsed = JSON.parse(rawArgs) as { citations?: Citation[] };
    return Array.isArray(parsed.citations) ? parsed.citations : [];
  } catch {
    return [];
  }
}
