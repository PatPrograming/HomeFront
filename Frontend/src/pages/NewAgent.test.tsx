// @vitest-environment jsdom
// Needs devDependencies the project does not have yet: jsdom and @testing-library/react.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import type { AgentType, Tool } from "../lib/agents";
import type { PermissionOption } from "../lib/permissions";

vi.mock("../components/Layout", () => ({ default: ({ children }: { children: unknown }) => children }));

const opt = (o: Partial<PermissionOption> & { name: string }): PermissionOption => ({
  label: o.name, effect: "does a thing", risk: "low", outbound: false, held_for_review: false,
  allowed_for_kind: true, allowed_for_role: true, recommended: false, reason_unavailable: null, ...o,
});
const OPTIONS: PermissionOption[] = [
  opt({ name: "email.send", label: "Send email", effect: "Send an email to one person", risk: "high", outbound: true, held_for_review: true, recommended: true }),
  opt({ name: "email.send_list", label: "Email the mailing list", effect: "Email everyone on the office mailing list", risk: "high", outbound: true, held_for_review: true }),
  opt({ name: "report.save", label: "Save reports", effect: "Save a report you can read later" }),
  opt({ name: "social.post", label: "Post to social media", effect: "Post to the office social accounts", risk: "high", outbound: true, held_for_review: true, allowed_for_kind: false, reason_unavailable: "Email agents can't post to social media." }),
  opt({ name: "web.scrape", label: "Read web pages", effect: "Read public web pages", allowed_for_role: false, reason_unavailable: "Your role (Caseworker) can't read arbitrary web pages." }),
];

const TYPE: AgentType = {
  kind: "email", label: "Email", description: "Drafts email", default_instructions: "Be brief.",
  // Deliberately NOT the recommended set: the picker must follow the server's recommendation.
  default_tools: ["email.send", "email.send_list"], allowed_tools: null, default_max_steps: 10,
};
const TOOLS: Tool[] = OPTIONS.map((o) => ({ name: o.name, description: o.effect, risk_level: o.risk }));

const api = {
  agentTypes: vi.fn(async () => [TYPE]),
  tools: vi.fn(async () => TOOLS),
  agentPermissions: vi.fn(async (kind: string) => ({ kind, role: "chief_of_staff", role_label: "Chief of Staff", tools: OPTIONS })),
  createAgent: vi.fn(async (body: { name: string }) => ({ agent_id: "agent-1", ...body })),
};
vi.mock("../lib/agentsApi", () => ({ agentsApi: api }));

const { default: NewAgent } = await import("./NewAgent");

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={["/agents/new?kind=email"]}>
      <NewAgent />
    </MemoryRouter>,
  );
// Match the dotted name as a whole token, so "email.send" does not also match "email.send_list".
const box = async (name: string) =>
  (await screen.findByRole("checkbox", {
    name: new RegExp(`(?<![\\w.])${name.replace(/\./g, "\\.")}(?![\\w.])`, "i"),
  })) as HTMLInputElement;
const panel = () => screen.getByRole("complementary");

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("NewAgent permission picker", () => {
  it("asks the server for the permissions of the chosen kind", async () => {
    renderPage();
    await box("email.send");
    expect(api.agentPermissions).toHaveBeenCalledWith("email");
  });

  it("pre-checks exactly the recommended set, not the type's default tools", async () => {
    renderPage();
    expect((await box("email.send")).checked).toBe(true);
    expect((await box("email.send_list")).checked).toBe(false);
    expect((await box("report.save")).checked).toBe(false);
  });

  it("disables a tool the kind doesn't allow and shows the reason", async () => {
    renderPage();
    expect((await box("social.post")).disabled).toBe(true);
    expect(await screen.findByText("Email agents can't post to social media.")).toBeTruthy();
  });

  it("disables a tool the role doesn't allow and shows the reason", async () => {
    renderPage();
    expect((await box("web.scrape")).disabled).toBe(true);
    expect(await screen.findByText("Your role (Caseworker) can't read arbitrary web pages.")).toBeTruthy();
  });

  it("leaves available tools enabled", async () => {
    renderPage();
    expect((await box("email.send_list")).disabled).toBe(false);
  });

  it("puts the changes panel in its own complementary region, outside the form", async () => {
    renderPage();
    await box("email.send");
    expect(panel().closest("form")).toBeNull();
    expect(panel().getAttribute("aria-live")).toBe("polite");
  });

  it("says there are no changes while the recommendation is untouched", async () => {
    renderPage();
    await box("email.send");
    expect(within(panel()).getByText(/matches the recommended/i)).toBeTruthy();
  });

  it("explains a removed recommended tool in plain words when it is unchecked", async () => {
    renderPage();
    fireEvent.click(await box("email.send"));
    await waitFor(() => expect(within(panel()).getByText(/Send an email to one person/)).toBeTruthy());
    expect(within(panel()).getByText(/removed/i)).toBeTruthy();
    expect(within(panel()).queryByText(/beyond/i)).toBeNull();
  });

  it("explains an added tool, marks it beyond the recommendation, and says outbound tools are held for review", async () => {
    renderPage();
    fireEvent.click(await box("email.send_list"));
    await waitFor(() => expect(within(panel()).getByText(/Email everyone on the office mailing list/)).toBeTruthy());
    expect(within(panel()).getByText(/beyond the recommendation/i)).toBeTruthy();
    expect(within(panel()).getByText(/held for review/i)).toBeTruthy();
  });

  it("does not say 'held for review' for an added tool that sends nothing", async () => {
    renderPage();
    fireEvent.click(await box("report.save"));
    await waitFor(() => expect(within(panel()).getByText(/Save a report you can read later/)).toBeTruthy());
    expect(within(panel()).queryByText(/held for review/i)).toBeNull();
  });

  it("drops the panel entry when the user undoes a change", async () => {
    renderPage();
    const list = await box("email.send_list");
    fireEvent.click(list);
    await waitFor(() => expect(within(panel()).getByText(/Email everyone on the office mailing list/)).toBeTruthy());
    fireEvent.click(list);
    await waitFor(() => expect(within(panel()).queryByText(/Email everyone on the office mailing list/)).toBeNull());
  });
});

describe("NewAgent submit", () => {
  async function fillAndSubmit() {
    fireEvent.change(await screen.findByPlaceholderText(/e\.g\. Email agent/i), { target: { value: "Outreach" } });
    fireEvent.change(screen.getByPlaceholderText(/Department of Housing/i), { target: { value: "Housing" } });
    fireEvent.click(screen.getByRole("button", { name: /create email agent/i }));
  }

  it("sends the recommended set untouched as tool_grants", async () => {
    renderPage();
    await box("email.send");
    await fillAndSubmit();
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    expect((api.createAgent.mock.calls[0][0] as { tool_grants: string[] }).tool_grants).toEqual(["email.send"]);
  });

  it("sends exactly the user's selection after adding and removing tools", async () => {
    renderPage();
    fireEvent.click(await box("email.send"));
    fireEvent.click(await box("report.save"));
    fireEvent.click(await box("email.send_list"));
    await fillAndSubmit();
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    const grants = (api.createAgent.mock.calls[0][0] as { tool_grants: string[] }).tool_grants;
    expect([...grants].sort()).toEqual(["email.send_list", "report.save"]);
  });

  it("never sends a disabled tool", async () => {
    renderPage();
    fireEvent.click(await box("social.post"));
    fireEvent.click(await box("web.scrape"));
    await fillAndSubmit();
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    expect((api.createAgent.mock.calls[0][0] as { tool_grants: string[] }).tool_grants).toEqual(["email.send"]);
  });

  it("shows the server's 403 message and stays on the form", async () => {
    api.createAgent.mockRejectedValueOnce(new ApiError(403, { detail: { error: "role_not_allowed", message: "Your role can't give an agent email.send_list." } }));
    renderPage();
    fireEvent.click(await box("email.send_list"));
    await fillAndSubmit();
    expect((await screen.findByRole("alert")).textContent).toContain("Your role can't give an agent email.send_list.");
    expect(screen.getByRole("button", { name: /create email agent/i })).toBeTruthy();
  });
});
