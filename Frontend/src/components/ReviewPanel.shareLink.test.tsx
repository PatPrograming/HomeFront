// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Draft } from "../lib/api";

const URL = "https://x.com/intent/post?text=Town%20hall%20Tuesday";
const approveDraft = vi.fn(async () => ({ id: "d2", status: "share_link", url: URL }));
vi.mock("../lib/api", () => ({ api: { approveDraft } }));
const { default: ReviewPanel } = await import("./ReviewPanel");

const post: Draft = {
  id: "d2", status: "pending", tool: "social_post", category: "social_post", categoryLabel: "Posts", urgency: "medium", score: 40, reason: "r",
  args: { account: "official", text: "Town hall Tuesday" }, createdAt: "2026-11-08T08:00:00Z", sid: "sid-1",
};

afterEach(cleanup);

describe("ReviewPanel: approving an X post with no X connected", () => {
  it("opens X's compose page with the text and shows an Open on X link", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const onClose = vi.fn();
    render(<ReviewPanel draft={post} locked={true} onDone={async () => {}} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve and post" }));
    const link = await screen.findByRole("link", { name: "Open on X" });
    expect(link.getAttribute("href")).toBe(URL);
    expect(open).toHaveBeenCalledWith(URL, "_blank", "noopener");
    expect(screen.getByText(/Opened on X — press Post there/)).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });
});
