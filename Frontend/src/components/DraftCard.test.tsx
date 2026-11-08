import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Draft } from "../lib/api";
import type { DraftCardViewProps } from "./DraftCardView";

const api = { updateDraft: vi.fn(async () => ({})), reviseDraft: vi.fn(async () => ({})) };
vi.mock("../lib/api", () => ({ api }));
const { DraftCardView } = await import("./DraftCardView");

const email: Draft = {
  id: "e1", sid: "s", status: "pending", tool: "email_send", category: "case_status", categoryLabel: "Case status updates",
  args: { to: "marcus.bell@example.com", subject: "Re: Post office", body: "Hi Marcus, we called USPS." },
  createdAt: "2026-11-08T08:00:00Z", urgency: "medium", score: 40, reason: "r", version: 2, editable: ["subject", "body"],
};
const post: Draft = {
  ...email, id: "p1", tool: "social_post", category: "social_post", categoryLabel: "Posts on the official account",
  args: { account: "official", text: "Pandemic update: vaccines at the library.", platform: "x" }, version: 1, editable: ["text", "platform"],
  platform: "x", recipient: null, platformOptions: [{ id: "x", label: "X" }, { id: "bluesky", label: "Bluesky" }],
};
const props = (over: Partial<DraftCardViewProps> = {}): DraftCardViewProps => ({
  draft: email, editing: false, edits: {}, instruction: "", busy: false, error: null,
  onOpen: () => {}, onEdit: () => {}, onChange: () => {}, onSave: () => {}, onInstruction: () => {}, onRevise: () => {}, ...over,
});

type P = { onClick?: () => void; onChange?: (e: { target: { value: string } }) => void; children?: ReactNode; "aria-label"?: string; readOnly?: boolean; value?: string };
function all(node: ReactNode): ReactElement<P>[] {
  if (Array.isArray(node)) return node.flatMap(all);
  if (!isValidElement(node)) return [];
  const el = node as ReactElement<P>;
  return [el, ...all(el.props.children)];
}
const text = (n: ReactNode) => renderToStaticMarkup(<>{n}</>).replace(/<[^>]+>/g, "");
const btn = (tree: ReactNode, label: string) => all(tree).find((e) => e.type === "button" && (e.props["aria-label"] === label || text(e.props.children).trim() === label))!;
const field = (tree: ReactNode, label: string) => all(tree).find((e) => (e.type === "input" || e.type === "textarea") && e.props["aria-label"] === label)!;

beforeEach(() => vi.clearAllMocks());

describe("DraftCardView: the draft in the chat", () => {
  it("shows the version, who it goes to and the text, and still offers the review path", () => {
    const html = renderToStaticMarkup(<DraftCardView {...props()} />);
    for (const shown of ["Draft 2", "To marcus.bell@example.com", "Re: Post office", "Hi Marcus, we called USPS.", "needs your review"]) expect(html).toContain(shown);
    const onOpen = vi.fn();
    btn(DraftCardView(props({ onOpen })), "Review: Reply to marcus.bell@example.com").props.onClick!();
    expect(onOpen).toHaveBeenCalledWith("e1");
  });

  it("a post shows its platforms clearly", () => {
    const html = renderToStaticMarkup(<DraftCardView {...props({ draft: post })} />);
    expect(html).toContain("Post on X");
    expect(html).toContain("Pandemic update");
  });

  it("editing in place: only the server's editable fields change; the recipient stays read-only", () => {
    const onChange = vi.fn();
    const tree = DraftCardView(props({ editing: true, onChange }));
    expect(field(tree, "Recipient").props.readOnly).toBe(true);
    expect(field(tree, "Subject").props.readOnly).toBeFalsy();
    field(tree, "Message").props.onChange!({ target: { value: "Shorter." } });
    expect(onChange).toHaveBeenCalledWith({ body: "Shorter." });
  });

  it("a post goes to one platform: the picker selects exactly one", () => {
    const onChange = vi.fn();
    const tree = DraftCardView(props({ draft: post, editing: true, onChange }));
    const html = renderToStaticMarkup(<>{tree}</>);
    expect(html).toContain('role="radiogroup"');
    expect(html).toMatch(/role="radio"[^>]*aria-checked="true"[^>]*>X</);
    expect(html).toMatch(/role="radio"[^>]*aria-checked="false"[^>]*>Bluesky</);
    btn(tree, "Bluesky").props.onClick!();
    expect(onChange).toHaveBeenCalledWith({ platform: "bluesky" });
  });

  it("Save edits sends the edits; asking for a change sends the instruction", () => {
    const onSave = vi.fn();
    const onRevise = vi.fn();
    btn(DraftCardView(props({ editing: true, edits: { body: "Shorter." }, onSave })), "Save edits").props.onClick!();
    expect(onSave).toHaveBeenCalledWith({ body: "Shorter." });
    btn(DraftCardView(props({ instruction: "make it shorter", onRevise })), "Revise").props.onClick!();
    expect(onRevise).toHaveBeenCalledWith("make it shorter");
    expect(renderToStaticMarkup(<DraftCardView {...props()} />)).toMatch(/aria-label="Ask for changes to this draft"/);
  });

  it("while the agent revises, the card says so and can't be edited", () => {
    const html = renderToStaticMarkup(<DraftCardView {...props({ draft: { ...email, revising: true } })} />);
    expect(html).toContain("Change requested…");
    expect(html).not.toContain(">Edit<");
  });
});

describe("DraftCard: edits and revisions reach the server", () => {
  let view: DraftCardViewProps;
  beforeEach(() => {
    vi.resetModules();
  });

  it("Save edits is a PATCH of the changed fields; Revise is a POST of the instruction", async () => {
    vi.doMock("./DraftCardView", () => ({
      DraftCardView: (p: DraftCardViewProps) => {
        view = p;
        return null;
      },
    }));
    vi.doMock("../lib/api", () => ({ api }));
    const { default: DraftCard } = await import("./DraftCard");
    const onChanged = vi.fn(async () => {});
    renderToStaticMarkup(<DraftCard draft={email} onOpen={() => {}} onChanged={onChanged} />);
    await view.onSave({});
    expect(api.updateDraft).not.toHaveBeenCalled(); // nothing changed: nothing to save
    await view.onSave({ body: "Shorter." });
    expect(api.updateDraft).toHaveBeenCalledWith("e1", { body: "Shorter.", expectedVersion: 2 }); // only edit the version you saw
    await view.onRevise("   ");
    expect(api.reviseDraft).not.toHaveBeenCalled(); // no instruction: nothing to ask
    await view.onRevise("make it shorter");
    expect(api.reviseDraft).toHaveBeenCalledWith("e1", "make it shorter");
    expect(onChanged).toHaveBeenCalledTimes(2); // the card reloads after each
  });
});

describe("DraftCard: someone else changed the draft first", () => {
  it("a stale version reloads the drafts and says why", async () => {
    vi.resetModules();
    let view: DraftCardViewProps | undefined;
    vi.doMock("./DraftCardView", () => ({
      DraftCardView: (p: DraftCardViewProps) => {
        view = p;
        return null;
      },
    }));
    const stale = Object.assign(new Error("conflict"), { status: 409, data: { error: "stale_version", draft: { ...email, version: 3 } } });
    vi.doMock("../lib/api", () => ({ api: { ...api, updateDraft: vi.fn(async () => Promise.reject(stale)) } }));
    const { staleMessage } = await import("./DraftCard");
    expect(staleMessage(stale)).toBe("This draft changed while you were editing; the latest version is shown.");
    expect(staleMessage(new Error("other"))).toBeNull();
    const { default: DraftCard } = await import("./DraftCard");
    const onChanged = vi.fn(async () => {});
    renderToStaticMarkup(<DraftCard draft={email} onOpen={() => {}} onChanged={onChanged} />);
    await view!.onSave({ body: "x" });
    expect(onChanged).toHaveBeenCalled(); // reloaded to the latest version
  });
});
