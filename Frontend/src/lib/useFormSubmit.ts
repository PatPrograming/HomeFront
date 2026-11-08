import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "./api.js";

export type SubmitState =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "success" }
  | { status: "error"; message: string };

export const SAVE_FAILED = "data failed to save";
export const PAGE_FAILED = "webpage failed to load";

// Unreachable API (fetch threw -> status 0) or a gateway/proxy failure means the page
// itself couldn't talk to the server; any other error means the server rejected the data.
function describeError(err: unknown): string {
  const status = (err as { status?: number })?.status;
  if (status === 0 || status === 502 || status === 503 || status === 504) return PAGE_FAILED;
  return SAVE_FAILED;
}

const REDIRECT_DELAY_MS = 1800;

/**
 * Submits a form to the backend and drives the confirmation popup:
 * idle -> saving -> success (then redirects to /monitoring) | error (dismiss() returns to the form).
 */
export function useFormSubmit(kind: "emails" | "social", onSaved?: () => void) {
  const navigate = useNavigate();
  const [state, setState] = useState<SubmitState>({ status: "idle" });
  const timer = useRef<number | undefined>(undefined);
  const mounted = useRef(true);
  const busy = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      window.clearTimeout(timer.current);
    };
  }, []);

  const submit = useCallback(
    async (data: Record<string, unknown>) => {
      if (busy.current) return;
      busy.current = true;
      setState({ status: "saving" });
      try {
        await api.saveForm(kind, data);
      } catch (err) {
        busy.current = false;
        if (mounted.current) setState({ status: "error", message: describeError(err) });
        return;
      }
      busy.current = false;
      if (!mounted.current) return;
      onSaved?.();
      setState({ status: "success" });
      timer.current = window.setTimeout(() => navigate("/monitoring"), REDIRECT_DELAY_MS);
    },
    [kind, navigate, onSaved],
  );

  const dismiss = useCallback(() => setState({ status: "idle" }), []);

  return { state, submit, dismiss };
}
