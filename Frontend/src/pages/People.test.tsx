import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { Person, RoleChange, RoleView, ToolInfo } from "../lib/api";

// People & roles, rendered with fixed data. The server enforces everything; these check what the admin reads.
vi.mock("../components/Layout", () => ({ default: ({ children }: { children: unknown }) => children }));
vi.mock("../lib/api", () => ({ api: {} }));

const { ChangeLog, ChangeWarning, PersonRow, RoleCard, toggleTool } = await import("./People");

const tools: ToolInfo[] = [
  { name: "case_read", action: "open constituent case files", alwaysDenied: false, lockedTo: [] },
  { name: "web_scrape", action: "read public web pages", alwaysDenied: false, lockedTo: [] },
  { name: "email_send_list", action: "email the whole office mailing list", alwaysDenied: false, lockedTo: ["Chief of Staff"] },
  { name: "records_bulk_export", action: "export constituent records in bulk", alwaysDenied: true, lockedTo: [] },
];
const intern: RoleView = {
  id: "intern", label: "Intern", tools: ["web_scrape"], defaultTools: ["web_scrape"],
  assignable: ["case_read", "web_scrape"], customized: false, updatedBy: null, updatedAt: null, people: ["aws"],
};
const roles: RoleView[] = [
  { ...intern, id: "caseworker", label: "Caseworker", tools: ["case_read", "web_scrape"], people: ["maya"] },
  intern,
];
const maya: Person = {
  username: "maya", email: "maya@example.gov", inDirectory: true, enabled: true, group: "staffer",
  officeRole: "caseworker", roleLabel: "Caseworker", office: "rep-x", active: true,
};
const noop = () => {};

describe("People & roles", () => {
  it("shows what each role may do in plain words, not tool ids or Cedar policy names", () => {
    const html = renderToStaticMarkup(<RoleCard role={intern} tools={tools} onSaved={noop} />);
    expect(html).toContain("Intern");
    expect(html).toContain("read public web pages");
    expect(html).toContain("1 person");
    for (const id of ["web_scrape", "role-web-scrape", "roleTools"]) expect(html).not.toContain(id);
  });

  it("warns what a change allows and who it reaches, using the server's words", () => {
    const change: RoleChange = {
      role: "intern", tools: ["case_read", "web_scrape"], before: ["web_scrape"], affects: ["aws"],
      changes: ["Every Intern's assistant will be able to open constituent case files."],
    };
    const html = renderToStaticMarkup(<ChangeWarning change={change} />);
    expect(html).toContain("What this change does");
    expect(html).toContain("Every Intern&#x27;s assistant will be able to open constituent case files.");
    expect(html).toContain("This applies to aws on their assistant&#x27;s very next action");
    expect(renderToStaticMarkup(<ChangeWarning change={{ ...change, changes: [] }} />)).toContain("No change yet");
  });

  it("lists every office role for a person and shows their current one", () => {
    const html = renderToStaticMarkup(<PersonRow person={maya} roles={roles} onSaved={noop} />);
    expect(html).toContain("maya");
    expect(html).toMatch(/<option value="caseworker" selected="">Caseworker<\/option>/);
    expect(html).toContain('<option value="intern">Intern</option>');
    expect(html).toContain("Admin (this page)");
    expect(html).not.toContain("No role"); // she has one
  });

  it("offers 'No role' only to someone without one, and cannot change the group of someone with no login", () => {
    const html = renderToStaticMarkup(
      <PersonRow person={{ ...maya, officeRole: null, roleLabel: "No role", inDirectory: false, group: null }} roles={roles} onSaved={noop} />,
    );
    expect(html).toContain('<option value="" selected="">No role</option>');
    expect(html).toContain("no login");
    expect(html).toMatch(/<select disabled="">\s*<option value="" selected="">No group/);
  });

  it("greys out a locked tool and a blocked tool in the editor so they cannot be ticked", async () => {
    const { RoleEditorFields } = await import("./People");
    const html = renderToStaticMarkup(<RoleEditorFields role={intern} tools={tools} draft={["web_scrape"]} busy={false} onPick={noop} />);
    expect(html).toContain("email the whole office mailing list (locked by policy to the Chief of Staff)");
    expect(html).toContain("export constituent records in bulk (blocked for everyone)");
    expect(html.match(/disabled=""/g)?.length).toBe(2);
  });

  it("toggles one tool at a time", () => {
    expect(toggleTool(["web_scrape"], "case_read")).toEqual(["web_scrape", "case_read"]);
    expect(toggleTool(["web_scrape", "case_read"], "web_scrape")).toEqual(["case_read"]);
  });

  it("shows the change log newest first as the server sends it", () => {
    const html = renderToStaticMarkup(
      <ChangeLog
        events={[
          { id: "2", ts: "2026-11-08T10:00:02Z", actor: "chiefofstaff", type: "role_assigned", target: "maya", summary: "maya is now Intern (was Caseworker)." },
          { id: "1", ts: "2026-11-08T10:00:01Z", actor: "chiefofstaff", type: "role_tools_changed", target: "intern", summary: "Every Intern's assistant will be able to send email." },
        ]}
      />,
    );
    expect(html.indexOf("maya is now Intern")).toBeLessThan(html.indexOf("Every Intern"));
    expect(renderToStaticMarkup(<ChangeLog events={[]} />)).toContain("No changes yet.");
  });
});
