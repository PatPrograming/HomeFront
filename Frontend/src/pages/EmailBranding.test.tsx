import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { Agent } from "../lib/agents";

// api.ts reads window at import (auth config); nothing here makes a request.
vi.mock("../lib/api", () => ({ ApiError: class ApiError extends Error {} }));
vi.mock("../lib/agentsApi", () => ({ agentsApi: { emailPreview: () => new Promise(() => {}) } }));
import EmailBranding, { logoProblem, MAX_LOGO_BYTES } from "./EmailBranding";

const agent = (logo: Agent["email_logo"] = null) =>
  ({ agent_id: "a-1", name: "Comms", agency: "City of Springfield", email_logo: logo }) as unknown as Agent;

describe("EmailBranding", () => {
  it("accepts PNG/JPEG/GIF up to 512 KB and explains anything else", () => {
    expect(logoProblem({ type: "image/png", size: 10_000 })).toBeNull();
    expect(logoProblem({ type: "image/jpeg", size: MAX_LOGO_BYTES })).toBeNull();
    expect(logoProblem({ type: "image/svg+xml", size: 100 })).toMatch(/SVG/);
    expect(logoProblem({ type: "image/png", size: MAX_LOGO_BYTES + 1 })).toMatch(/512 KB/);
  });

  it("offers upload without a logo, replace and remove with one", () => {
    const none = renderToStaticMarkup(<EmailBranding agent={agent()} onSaved={() => {}} />);
    expect(none).toContain("Upload logo");
    expect(none).not.toContain("Remove");
    expect(none).toContain("City of Springfield");

    const meta = { content_type: "image/png", size: 1000, width: 120, height: 60, uploaded_at: 1 };
    const some = renderToStaticMarkup(<EmailBranding agent={agent(meta)} onSaved={() => {}} />);
    expect(some).toContain("Replace logo");
    expect(some).toContain("Remove");
  });
});
