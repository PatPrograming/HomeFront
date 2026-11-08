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

const { default: Account } = await import("./Account");

// ---- fixtures: GET /connections entries, exactly the shape ConnectionService.list() returns (never a token)
const entry = (provider: string, label: string, over: Record<string, unknown> = {}) => ({
  provider, label, supported: true, configured: true, connected: false, handle: null, connectedAt: null, scopes: "", ...over,
});
const unsupported = (provider: string, label: string) => entry(provider, label, { supported: false, configured: false });

let connections: any[] = [];
const me = { username: "maya", sub: "sub-1", email: "maya@example.com", groups: ["staff"] };

function serve(extra: Record<string, (body: any) => [number, unknown]> = {}) {
  h.setRoutes({
    "GET /api/v1/me": () => [200, me],
    "GET /api/v1/connections": () => [200, { connections }],
    "DELETE /api/v1/connections/gmail": () => {
      connections = connections.map((c) => (c.provider === "gmail" ? { ...c, connected: false, handle: null, connectedAt: null } : c));
      return [200, { provider: "gmail", connected: false }];
    },
    "POST /api/v1/connections/gmail/start": () => [200, { authorizeUrl: "https://accounts.google.com/o/oauth2/v2/auth?state=abc" }],
    ...extra,
  });
}

async function open(search = "") {
  setUrl(search);
  const { createElement } = await import("react");
  h.mount(createElement(Account));
  await h.settle();
}

describe("Account: connected accounts", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", h.fetch);
    h.assign.mockReset();
    connections = [
      entry("gmail", "Gmail", { connected: true, handle: "maya@gmail.com", connectedAt: "2026-11-08T10:00:00Z", scopes: "openid email" }),
      entry("x", "X", { configured: false }),
      unsupported("linkedin", "LinkedIn"),
      unsupported("facebook", "Facebook"),
      unsupported("bluesky", "Bluesky"),
    ];
    serve();
  });

  it("shows the signed-in email from /me instead of a dash", async () => {
    await open();
    const row = h.factsRow("Email");
    expect(row).toContain("maya@example.com");
    expect(row).not.toContain("—");
  });

  it("lists Gmail, X, LinkedIn, Facebook and Bluesky under a Connected accounts heading, fetched with GET /connections", async () => {
    await open();
    const text = h.text();
    expect(text).toContain("Connected accounts");
    for (const label of ["Gmail", "X", "LinkedIn", "Facebook", "Bluesky"]) expect(text).toContain(label);
    expect(h.calls.map((c) => c.key)).toContain("GET /api/v1/connections");
  });

  it("shows 'Connected as <handle>' and a Disconnect button for a connected account", async () => {
    await open();
    expect(h.text()).toContain("Connected as maya@gmail.com");
    expect(h.buttons("Disconnect")).toHaveLength(1);
    expect(h.buttons("Connect Gmail")).toHaveLength(0);
  });

  it("Disconnect calls DELETE /connections/gmail and then shows Not connected", async () => {
    await open();
    h.click("Disconnect");
    await h.settle();
    expect(h.calls.map((c) => c.key)).toContain("DELETE /api/v1/connections/gmail");
    expect(h.text()).not.toContain("Connected as");
    expect(h.text()).toContain("Not connected");
    expect(h.buttons("Connect Gmail")).toHaveLength(1);
  });

  it("a configured, not-connected account offers 'Connect Gmail', which starts OAuth and sends the browser to the authorize URL", async () => {
    connections[0] = entry("gmail", "Gmail");
    await open();
    expect(h.text()).toContain("Not connected");
    h.click("Connect Gmail");
    await h.settle();
    const start = h.calls.filter((c) => c.key === "POST /api/v1/connections/gmail/start");
    expect(start).toHaveLength(1);
    expect(h.assign).toHaveBeenCalledTimes(1);
    expect(h.assign).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?state=abc");
  });

  it("a supported provider that is not configured says 'Not set up yet — ask your admin' and has no enabled Connect button", async () => {
    await open();
    expect(h.text()).toContain("Not set up yet — ask your admin");
    for (const b of h.buttons(/^Connect X$/)) expect(b.props.disabled).toBe(true);
  });

  it("an unsupported provider says 'Not supported yet' and has no enabled Connect button", async () => {
    await open();
    expect(h.text()).toContain("Not supported yet");
    for (const label of ["LinkedIn", "Facebook", "Bluesky"]) {
      for (const b of h.buttons(new RegExp(`^Connect ${label}$`))) expect(b.props.disabled).toBe(true);
    }
  });

  it("opening /account?connect=gmail does not start OAuth by itself", async () => {
    connections[0] = entry("gmail", "Gmail");
    await open("?connect=gmail");
    expect(h.text()).toContain("Gmail");
    expect(h.calls.filter((c) => c.key.endsWith("/start"))).toHaveLength(0);
    expect(h.calls.filter((c) => c.key.startsWith("POST"))).toHaveLength(0);
    expect(h.assign).not.toHaveBeenCalled();
  });
});
