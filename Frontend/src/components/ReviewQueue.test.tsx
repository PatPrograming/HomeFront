/* eslint-disable @typescript-eslint/no-explicit-any */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Same no-DOM hook harness as ConnectPrompt.test.tsx / Account.test.tsx: React hooks are replaced by an in-memory
// version, the tree is expanded to host nodes, and "clicking" calls a button's onClick. Only fetch is faked, so the
// real lib/api.ts runs against POST /drafts/{id}/approve.
const h = vi.hoisted(() => {
  type HNode = { type: string; props: Record<string, any>; children: Array<HNode | string> };
  const slots = new Map<string, any>();
  let path = "";
  let idx = 0;
  let rootEl: any = null;
  let tree: HNode = { type: "root", props: {}, children: [] };
  let queued = false;
  let effects: Array<() => unknown> = [];

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
    // Text of the first draft card (<li>).
    card: () => all((n) => n.type === "li").map(textOf)[0] ?? "",
    buttons: (name: string | RegExp) =>
      all((n) => n.type === "button").filter((b) => (typeof name === "string" ? textOf(b).trim() === name : name.test(textOf(b)))),
    click(name: string | RegExp) {
      const [b] = api.buttons(name);
      if (!b) throw new Error(`no button "${name}" on the page; page text: ${textOf(tree)}`);
      b.props.onClick?.({ preventDefault() {} });
    },
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
}));
vi.mock("../lib/mode", () => ({ MOCK: false }));
vi.mock("../lib/auth", () => ({ getAccessToken: () => "maya-access-token" }));

const { createElement } = await import("react");
const { default: ReviewQueue } = await import("./ReviewQueue");

const draft = {
  id: "d1",
  status: "pending",
  tool: "social_post",
  category: "social_post",
  categoryLabel: "Public posts",
  args: { account: "townhall", text: "Town hall Tuesday, 6 pm" },
  createdAt: "2026-11-08T08:00:00Z",
  sid: null,
};
const APPROVE = "POST /api/v1/drafts/d1/approve";

function mountQueue(onDone: () => Promise<void>) {
  h.mount(createElement(ReviewQueue, { drafts: [draft] as any, locked: new Set<string>(), onDone }));
}

describe("<ReviewQueue>: approve when the account is not connected", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", h.fetch);
  });

  it("shows Connect X with 'Nothing was sent' inside the draft's card, and keeps the draft pending", async () => {
    h.setRoutes({
      [APPROVE]: () => [
        200,
        { id: "d1", status: "needs_connection", provider: "x", label: "X", connectUrl: "/account?connect=x", note: "NOT sent." },
      ],
    });
    const onDone = vi.fn(async () => {});
    mountQueue(onDone);
    h.click("Approve and post");
    await h.settle();

    expect(h.calls.map((c) => c.key)).toContain(APPROVE);
    expect(h.buttons("Connect X")).toHaveLength(1);
    const card = h.card();
    expect(card).toContain("Nothing was sent");
    expect(card).toContain("Connect X");
    expect(card).toContain("Town hall Tuesday, 6 pm"); // draft still shown
    expect(onDone).not.toHaveBeenCalled(); // not treated as sent: the list is not refreshed or the draft removed
    expect(h.buttons("Approve and post")).toHaveLength(1); // can retry after connecting
  });

  it("shows no Connect button when the draft was sent", async () => {
    h.setRoutes({ [APPROVE]: () => [200, { id: "d1", status: "sent", edited: false }] });
    const onDone = vi.fn(async () => {});
    mountQueue(onDone);
    h.click("Approve and post");
    await h.settle();

    expect(h.buttons(/^Connect /)).toHaveLength(0);
    expect(h.text()).not.toContain("Nothing was sent");
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
