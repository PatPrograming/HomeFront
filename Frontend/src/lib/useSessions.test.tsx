import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// The hook's reload is what the poll and the search box call; check it asks the server with the query.
const listSessions = vi.fn(async () => ({ sessions: [] }));
vi.mock("./api", () => ({ api: { listSessions } }));
const { useSessions } = await import("./useSessions");

let reload: () => Promise<void> = async () => {};
function Probe({ q, archived }: { q: string; archived: boolean }) {
  reload = useSessions(q, archived).reload;
  return null;
}

describe("useSessions", () => {
  it("passes the search text and the archived view to GET /sessions", async () => {
    renderToStaticMarkup(<Probe q="pothole" archived />);
    await reload();
    expect(listSessions).toHaveBeenCalledWith("pothole", true);
  });
});
