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

const { default: ConnectCallback } = await import("./ConnectCallback");

const gmailEntry = {
  provider: "gmail", label: "Gmail", supported: true, configured: true, connected: true,
  handle: "maya@gmail.com", connectedAt: "2026-11-08T10:00:00Z", scopes: "openid email",
};

async function open(search: string) {
  setUrl(search);
  const { createElement } = await import("react");
  h.mount(createElement(ConnectCallback));
  await h.settle();
}
const callbackPosts = () => h.calls.filter((c) => c.key === "POST /api/v1/connections/callback");

describe("ConnectCallback (/account/connected)", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", h.fetch);
    h.setRoutes({ "POST /api/v1/connections/callback": () => [200, gmailEntry] });
  });

  it("posts the code and state to /connections/callback exactly once and names the provider on success", async () => {
    await open("?code=c&state=s");
    expect(callbackPosts()).toHaveLength(1);
    expect(callbackPosts()[0].body).toEqual({ code: "c", state: "s" });
    expect(h.text()).toContain("Gmail");
    expect(h.text()).toMatch(/connected/i);
    expect(h.links().map((a) => a.props.href)).toContain("/account");
  });

  it("does not post the code a second time when the page re-renders", async () => {
    await open("?code=c&state=s");
    await h.settle();
    expect(callbackPosts()).toHaveLength(1);
  });

  it("shows plain words and posts nothing when the provider sent ?error=access_denied", async () => {
    await open("?error=access_denied");
    expect(h.calls).toHaveLength(0);
    expect(h.text()).toMatch(/cancel|declin|denied|didn.t|did not|not connected/i);
    expect(h.text()).not.toContain("access_denied");
    expect(h.links().map((a) => a.props.href)).toContain("/account");
  });

  it("explains an expired or invalid sign-in link on a 400 and links back to /account", async () => {
    h.setRoutes({ "POST /api/v1/connections/callback": () => [400, { detail: "invalid_state" }] });
    await open("?code=c&state=old");
    expect(callbackPosts()).toHaveLength(1);
    expect(h.text()).toMatch(/expired|invalid/i);
    expect(h.text()).not.toContain("invalid_state");
    expect(h.text()).not.toContain("HTTP 400");
    expect(h.links().map((a) => a.props.href)).toContain("/account");
  });

  it("tells the person to tick every box on a 403 missing_permission, not that the link expired", async () => {
    h.setRoutes({
      "POST /api/v1/connections/callback": () => [
        403,
        { detail: "missing_permission: you signed in but did not allow sending on your behalf. Connect again and tick every box." },
      ],
    });
    await open("?code=c&state=s");
    expect(callbackPosts()).toHaveLength(1);
    expect(h.text()).toMatch(/allow|tick|permission/i);
    expect(h.text()).not.toMatch(/expired or invalid/i);
    expect(h.text()).not.toContain("missing_permission");
    expect(h.text()).not.toContain("HTTP 403");
    expect(h.links().map((a) => a.props.href)).toContain("/account");
  });
});
