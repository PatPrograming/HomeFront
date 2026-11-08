// Mock mode's connected accounts (VITE_MOCK_API=1, dev server only), answering Backend/app/routers/connections.py:
//   GET /connections, POST /connections/{provider}/start, POST /connections/callback, DELETE /connections/{provider}
// "Connecting" never leaves the app: start returns the callback page's own URL with a fake code.
import { MockError } from "./mockAssistant";
import type { ConnectionView } from "./connections";

const STORE_KEY = "mandate.mock.connections.v1";

const PROVIDERS: Array<Pick<ConnectionView, "provider" | "label" | "supported" | "configured"> & { handle: string }> = [
  { provider: "gmail", label: "Gmail", supported: true, configured: true, handle: "maya@gmail.com" },
  { provider: "x", label: "X", supported: true, configured: true, handle: "@maya_office" },
  { provider: "linkedin", label: "LinkedIn", supported: false, configured: false, handle: "" },
  { provider: "facebook", label: "Facebook", supported: false, configured: false, handle: "" },
  { provider: "bluesky", label: "Bluesky", supported: false, configured: false, handle: "" },
];

interface Saved {
  connected: Record<string, string>; // provider → ISO time it was connected
  pending: Record<string, string>; // state → provider
}

function load(): Saved {
  const raw = localStorage.getItem(STORE_KEY);
  return raw ? (JSON.parse(raw) as Saved) : { connected: {}, pending: {} };
}

function view(saved: Saved, provider: string): ConnectionView {
  const p = PROVIDERS.find((x) => x.provider === provider);
  if (!p) throw new MockError(404, "Unknown provider");
  const connectedAt = saved.connected[provider] ?? null;
  return {
    provider,
    label: p.label,
    supported: p.supported,
    configured: p.configured,
    connected: connectedAt !== null,
    handle: connectedAt ? p.handle : null,
    connectedAt,
    scopes: "",
  };
}

export function mockConnectionsRoute(method: string, pathname: string, body: unknown): unknown {
  const saved = load();
  const save = () => localStorage.setItem(STORE_KEY, JSON.stringify(saved));

  if (method === "GET" && pathname === "/connections") {
    return { connections: PROVIDERS.map((p) => view(saved, p.provider)) };
  }
  if (method === "POST" && pathname === "/connections/callback") {
    const { state } = body as { code: string; state: string };
    const provider = saved.pending[state];
    if (!provider) throw new MockError(400, "invalid_state");
    delete saved.pending[state]; // a state works once, as on the server
    saved.connected[provider] = new Date().toISOString();
    save();
    return view(saved, provider);
  }
  const start = pathname.match(/^\/connections\/([^/]+)\/start$/);
  if (method === "POST" && start) {
    const entry = view(saved, decodeURIComponent(start[1]));
    if (!entry.supported || !entry.configured) throw new MockError(409, "not_set_up");
    const state = crypto.randomUUID();
    saved.pending[state] = entry.provider;
    save();
    return { authorizeUrl: `/account/connected?code=mock&state=${state}` };
  }
  const one = pathname.match(/^\/connections\/([^/]+)$/);
  if (method === "DELETE" && one) {
    const provider = decodeURIComponent(one[1]);
    view(saved, provider);
    delete saved.connected[provider];
    save();
    return { provider, connected: false };
  }
  throw new MockError(404, `mock API has no ${method} ${pathname}`);
}
