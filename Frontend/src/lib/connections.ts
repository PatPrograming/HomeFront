import { request } from "./api";

/** One entry of GET /connections. The server never sends provider tokens, so there are none here. */
export interface ConnectionView {
  provider: string;
  label: string;
  supported: boolean;
  configured: boolean;
  connected: boolean;
  handle: string | null;
  connectedAt: string | null;
  scopes: string;
}

export const connectionsApi = {
  list: () => request<{ connections: ConnectionView[] }>("/connections").then((r) => r.connections),
  start: (provider: string) =>
    request<{ authorizeUrl: string }>(`/connections/${encodeURIComponent(provider)}/start`, { method: "POST", body: {} }).then(
      (r) => r.authorizeUrl,
    ),
  finish: (code: string, state: string) => request<ConnectionView>("/connections/callback", { method: "POST", body: { code, state } }),
  disconnect: (provider: string) =>
    request<{ provider: string; connected: false }>(`/connections/${encodeURIComponent(provider)}`, { method: "DELETE" }),
};

/** Sends the browser to the provider's consent screen. */
export async function beginConnect(provider: string): Promise<void> {
  window.location.assign(await connectionsApi.start(provider));
}
