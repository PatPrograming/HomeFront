import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { Draft } from "../lib/api";

vi.mock("../lib/api", () => ({ api: {} }));
const { default: ReviewPanel } = await import("./ReviewPanel");

const email: Draft = {
  id: "d1", status: "pending", tool: "email_send", category: "case_status", categoryLabel: "Case status updates",
  args: { to: "marcus.bell@example.com", subject: "Re: Post office", body: "Hi Marcus," }, createdAt: "2026-11-08T08:00:00Z", sid: "sid-1",
};
// The <input> tag showing `value`, and whether it has React's real readOnly attribute (rendered readonly="";
// matched case-sensitively, so aria-readonly or an "is-readonly" class does not count).
function inputTag(html: string, value: string): string {
  const tag = html.match(new RegExp(`<input[^>]*value="${value.replace(/[.@]/g, "\\$&")}"[^>]*>`))?.[0];
  if (!tag) throw new Error(`no input with value ${value}`);
  return tag;
}
const isReadOnly = (tag: string) => /\sreadonly=""/.test(tag);

const render = (draft: Draft, locked = false) =>
  renderToStaticMarkup(<ReviewPanel draft={draft} locked={locked} onDone={async () => {}} onClose={() => {}} />);

describe("ReviewPanel: one draft, opened from the chat", () => {
  it("shows the recipient read-only and the text editable", () => {
    const html = render(email);
    expect(isReadOnly(inputTag(html, "marcus.bell@example.com"))).toBe(true);
    expect(isReadOnly(inputTag(html, "Re: Post office"))).toBe(false);
    expect(html).toMatch(/<textarea(?![^>]*\sreadonly="")[^>]*>Hi Marcus,<\/textarea>/);
  });

  it("offers approve, allow for this session and reject, plus a close button", () => {
    const html = render(email);
    for (const label of ["Approve and send", "Approve and allow for this session", "Reject", "Close review"]) expect(html).toContain(label);
  });

  it("never offers 'allow for this session' for a locked category", () => {
    expect(render(email, true)).not.toContain("Approve and allow for this session");
  });

  it("shows the account read-only for a post", () => {
    const post: Draft = { ...email, id: "d2", tool: "social_post", category: "social_post", categoryLabel: "Posts", args: { account: "official", text: "Town hall" } };
    const html = render(post);
    expect(isReadOnly(inputTag(html, "@official"))).toBe(true);
    expect(html).toContain("Approve and post");
  });
});
