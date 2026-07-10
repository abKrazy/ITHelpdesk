import { CopilotRuntime, createCopilotEndpointSingleRoute } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const backendUrl = process.env.AGUI_BACKEND_URL || "http://localhost:8000/agui";

const copilotRuntime = new CopilotRuntime({
  agents: {
    helpdesk: new HttpAgent({
      agentId: "helpdesk",
      description: "ServiceNow IT helpdesk AG-UI backend",
      url: backendUrl,
    }) as never,
  },
});

const endpoint = createCopilotEndpointSingleRoute({
  runtime: copilotRuntime,
  basePath: "/api/copilotkit",
});

export async function POST(request: Request): Promise<Response> {
  return endpoint.fetch(request);
}

export async function OPTIONS(request: Request): Promise<Response> {
  return endpoint.fetch(request);
}
