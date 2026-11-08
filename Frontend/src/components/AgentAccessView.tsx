export type AgentCredentials = "limited-role" | "shared-server";

// Admins only: whether the chat agent runs with its own Bedrock-only role, or with the server's AWS credentials
// (a laptop without HOMEFRONT_AGENT_ROLE_ARN), which could reach the database around the gateway.
export function AgentAccessView({ mode }: { mode: AgentCredentials | null }) {
  if (mode === "shared-server") {
    return (
      <p className="alert" role="status">
        The agent shares the server's AWS credentials. Set <code>HOMEFRONT_AGENT_ROLE_ARN</code> (see{" "}
        <code>Backend/infra/agent-role.sh</code>) so it can only call Bedrock.
      </p>
    );
  }
  if (mode === "limited-role") return <p className="muted">The agent runs with its own Bedrock-only AWS role.</p>;
  return null;
}
