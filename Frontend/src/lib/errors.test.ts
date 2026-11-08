import { describe, expect, it } from "vitest";
import { connectionMessage } from "./errors";

describe("connectionMessage: plain words instead of browser errors", () => {
  it.each([new TypeError("Failed to fetch"), new TypeError("Load failed"), new TypeError("NetworkError when attempting to fetch resource."), new DOMException("timed out", "TimeoutError")])(
    "turns %s into the HomeFront message",
    (e) => {
      expect(connectionMessage(e, true)).toBe("Can't reach HomeFront's server. Retrying…");
      expect(connectionMessage(e, false)).toBe("Can't reach HomeFront's server. Check your connection and try again.");
    },
  );

  it("keeps the API's own words for a real answer from the server", () => {
    expect(connectionMessage(new Error("staffer is offboarded"), false)).toBe("staffer is offboarded"); // ApiError carries the detail as its message
  });
});
