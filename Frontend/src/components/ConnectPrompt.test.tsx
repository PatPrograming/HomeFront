/* eslint-disable @typescript-eslint/no-explicit-any */
import { beforeEach, describe, expect, it, vi } from "vitest";

// The Frontend test setup has no DOM and no testing-library (node environment, react-dom/server only), so these
// tests drive the real page with a tiny hook runtime: React's state/effect hooks are replaced by a small in-memory
// version, the element tree is expanded to plain host nodes, and "clicking" calls a button's onClick.
// Only fetch is faked (as in lib/api.test.ts), so the real lib/api.ts and lib/connections.ts run.
const h = vi.hoisted(() => {
  type HNode = { type: string; props: Record<string, any>; children: Array<HNode | string> };
  const slots = new Map<string, any>();
  let path = "";
  let idx = 0;
  let rootEl: any = null;
  let tree: HNode = { type: "root", props: {}, children: [] };
  let queued = false;
  let effects: Array<() => unknown> = [];

  const url = { search: "" };
  const assign = vi.fn();
  const calls: Array<{ key: string; body: any }> = [];
  let routes: Record<string, (body: any) => [number, unknown]> = {};

  function schedule() {
    if (queued) return;
    queued = true;
    queueMicrotask(() => {
      queued = false;
      render();
    });
  }
  const hooks = {
    useState(init: any) {
      const key = `${path}:${idx++}`;
      if (!slots.has(key)) slots.set(key, typeof init === "function" ? init() : init);
      const set = (v: any) => {
        const cur = slots.get(key);
        const next = typeof v === "function" ? v(cur) : v;
        if (!Object.is(next, cur)) {
          slots.set(key, next);
          schedule();
        }
      };
      return [slots.get(key), set];
    },
    useRef(init: any) {
      const key = `${path}:${idx++}`;
      if (!slots.has(key)) slots.set(key, { current: init });
      return slots.get(key);
    },
    useEffect(fn: () => unknown, deps?: any[]) {
      const key = `${path}:${idx++}`;
      const prev = slots.get(key);
      const changed = !prev || !deps || deps.length !== prev.deps.length || deps.some((d, i) => !Object.is(d, prev.deps[i]));
      if (changed) {
        slots.set(key, { deps });
        effects.push(fn);
      }
    },
    useCallback: (fn: any) => fn,
    useMemo: (fn: () => any) => fn(),
  };
  function expand(el: any, p: string): Array<HNode | string> {
    if (el == null || typeof el === "boolean") return [];
    if (typeof el === "string" || typeof el === "number") return [String(el)];
    if (Array.isArray(el)) return el.flatMap((c, i) => expand(c, `${p}[${i}]`));
    const { type, props } = el;
    const here = `${p}/${el.key ?? ""}${typeof type === "string" ? type : type?.name || "C"}`;
    if (typeof type === "string") return [{ type, props, children: expand(props.children, here) }];
    if (typeof type === "function") {
      const saved = [path, idx] as const;
      path = here;
      idx = 0;
      const out = type(props);
      path = saved[0];
      idx = saved[1];
      return expand(out, here);
    }
    return expand(props?.children, here);
  }
  function render() {
    effects = [];
    tree = { type: "root", props: {}, children: expand(rootEl, "R") };
    const run = effects;
    effects = [];
    run.forEach((f) => f());
  }
  const textOf = (n: HNode | string): string => (typeof n === "string" ? n : n.children.map(textOf).join(""));
  const all = (pred: (n: HNode) => boolean, n: HNode = tree): HNode[] =>
    n.children.flatMap((c) => (typeof c === "string" ? [] : [...(pred(c) ? [c] : []), ...all(pred, c)]));
  const api = {
    hooks,
    url,
    assign,
    calls,
    setRoutes(r: typeof routes) {
      routes = r;
      calls.length = 0;
    },
    async fetch(u: string, init: any = {}) {
      const key = `${init.method ?? "GET"} ${u}`;
      const body = init.body ? JSON.parse(init.body) : undefined;
      calls.push({ key, body });
      const route = routes[key];
      const [status, json] = route ? route(body) : [404, { detail: "no route" }];
      return new Response(JSON.stringify(json), { status });
    },
    mount(el: any) {
      slots.clear();
      rootEl = el;
      render();
    },
    async settle() {
      for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
    },
    text: () => textOf(tree),
    buttons: (name: string | RegExp) =>
      all((n) => n.type === "button").filter((b) => (typeof name === "string" ? textOf(b).trim() === name : name.test(textOf(b)))),
    links: () => all((n) => n.type === "a"),
    click(name: string | RegExp) {
      const [b] = api.buttons(name);
      if (!b) throw new Error(`no button "${name}" on the page; page text: ${textOf(tree)}`);
      b.props.onClick?.({ preventDefault() {} });
    },
    factsRow: (label: string) =>
      all((n) => n.type === "div" && n.children.some((c) => typeof c !== "string" && c.type === "dt" && textOf(c) === label)).map(
        textOf,
      )[0],
  };
  return api;
});

vi.mock("react", async (orig) => {
  const actual = await orig<typeof import("react")>();
  const merged = { ...actual, ...h.hooks, useLayoutEffect: h.hooks.useEffect };
  return { ...merged, default: merged };
});
vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: any) => ({ type: "a", props: { href: to, ...rest, children }, key: null }),
  useSearchParams: () => [new URLSearchParams(h.url.search), () => {}],
  useLocation: () => ({ pathname: "/", search: h.url.search }),
  useNavigate: () => () => {},
  Navigate: () => null,
}));
vi.mock("../components/Layout", () => ({ default: ({ children }: any) => children }));
vi.mock("../lib/AuthProvider", () => ({
  useSession: () => ({ username: "maya", expiresAt: Date.now() + 3600_000 }),
  useAuth: () => ({ signOut: () => {} }),
}));
vi.mock("../lib/mode", () => ({ MOCK: false }));
vi.mock("../lib/auth", () => ({ getAccessToken: () => "maya-access-token" }));

function setUrl(search: string) {
  h.url.search = search;
  const location = { search, pathname: "/account", assign: (to: string) => h.assign(to) };
  vi.stubGlobal("location", location);
  vi.stubGlobal("window", { location, scrollTo: () => {} });
  vi.stubGlobal("document", { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] });
}

const { default: ConnectPrompt, parseNeedsConnection } = await import("./ConnectPrompt");

const result = {
  status: "needs_connection",
  provider: "gmail",
  label: "Gmail",
  connectUrl: "/account?connect=gmail",
  note: "NOT sent. Maya has not connected Gmail. Ask them to connect it on the Account page, then try again.",
};
const parsed = { provider: "gmail", label: "Gmail", note: result.note };

describe("parseNeedsConnection", () => {
  it("reads the needs_connection result object", () => {
    expect(parseNeedsConnection(result)).toEqual(parsed);
  });

  it("reads the result from a JSON string", () => {
    expect(parseNeedsConnection(JSON.stringify(result))).toEqual(parsed);
  });

  it("finds the result inside surrounding text", () => {
    const text = `I tried to send it. ${JSON.stringify(result)} Let me know once that is done.`;
    expect(parseNeedsConnection(text)).toEqual(parsed);
  });

  it("returns null for unrelated input and held_for_review results", () => {
    expect(parseNeedsConnection("Sent your email to Dana.")).toBeNull();
    expect(parseNeedsConnection(null)).toBeNull();
    expect(parseNeedsConnection(42)).toBeNull();
    expect(parseNeedsConnection({ status: "held_for_review", draftId: "d1" })).toBeNull();
    expect(parseNeedsConnection(JSON.stringify({ status: "held_for_review", draftId: "d1" }))).toBeNull();
    expect(parseNeedsConnection('{"status":"sent","messageId":"m1"}')).toBeNull();
  });
});

describe("<ConnectPrompt>", () => {
  beforeEach(async () => {
    setUrl("");
    vi.stubGlobal("fetch", h.fetch);
    h.assign.mockReset();
    h.setRoutes({
      "POST /api/v1/connections/gmail/start": () => [200, { authorizeUrl: "https://accounts.google.com/o/oauth2/v2/auth?state=abc" }],
    });
    const { createElement } = await import("react");
    h.mount(createElement(ConnectPrompt, { provider: "gmail", label: "Gmail" }));
  });

  it("says nothing was sent and offers to connect Gmail", () => {
    expect(h.text()).toContain("Nothing was sent");
    expect(h.buttons("Connect Gmail")).toHaveLength(1);
  });

  it("Connect Gmail starts the connect flow: POST /connections/gmail/start, then the browser goes to the authorize URL", async () => {
    h.click("Connect Gmail");
    await h.settle();
    expect(h.calls.map((c) => c.key)).toEqual(["POST /api/v1/connections/gmail/start"]);
    expect(h.assign).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?state=abc");
  });

  it("links to the Account page to manage connected accounts", () => {
    expect(h.links().map((a) => a.props.href)).toContain("/account");
  });
});

describe("parseNeedsConnection hardening", () => {
  it("returns null unless status is exactly needs_connection", () => {
    expect(parseNeedsConnection({ provider: "gmail", label: "Gmail", status: "sent" })).toBeNull();
    expect(parseNeedsConnection({ provider: "gmail", label: "Gmail" })).toBeNull();
    expect(parseNeedsConnection(JSON.stringify({ provider: "gmail", label: "Gmail", status: "ok" }))).toBeNull();
  });

  it("only accepts provider gmail or x", () => {
    expect(parseNeedsConnection({ ...result, provider: "linkedin", label: "LinkedIn" })).toBeNull();
    expect(parseNeedsConnection({ ...result, provider: "evil", label: "Evil" })).toBeNull();
    expect(parseNeedsConnection({ ...result, provider: "x", label: "X" })?.provider).toBe("x");
  });

  it("takes the label from a fixed map, never from the input", () => {
    expect(parseNeedsConnection({ ...result, label: "Gmail (evil)" })?.label).toBe("Gmail");
    expect(parseNeedsConnection({ ...result, provider: "x", label: "X (evil)" })?.label).toBe("X");
  });
});

describe("parseNeedsConnection: Object.prototype names are not providers", () => {
  it.each(["__proto__", "toString", "constructor", "hasOwnProperty"])("returns null for provider %s (object and JSON string)", (name) => {
    const result = { status: "needs_connection", provider: name, label: name, note: "NOT sent." };
    expect(parseNeedsConnection(result)).toBeNull();
    expect(parseNeedsConnection(JSON.stringify(result))).toBeNull();
  });
});

describe("<ConnectPrompt> label", () => {
  it("shows Connect X for provider x even if the given label says otherwise", async () => {
    setUrl("");
    const { createElement } = await import("react");
    h.mount(createElement(ConnectPrompt, { provider: "x", label: "X (evil)" }));
    expect(h.buttons("Connect X")).toHaveLength(1);
    expect(h.text()).not.toContain("evil");
  });
});
