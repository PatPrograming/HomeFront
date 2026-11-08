import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { SubmitState } from "../../lib/useFormSubmit";
import "./SubmitOverlay.css";

interface Props {
  state: SubmitState;
  onClose: () => void;
}

// Full-screen confirmation popup shown while a form is being saved.
export default function SubmitOverlay({ state, onClose }: Props) {
  const open = state.status !== "idle";
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Make the rest of the app inert and unreachable by keyboard while the popup is up.
  useEffect(() => {
    if (!open) return;
    const root = document.getElementById("root");
    root?.setAttribute("inert", "");
    return () => root?.removeAttribute("inert");
  }, [open]);

  // Move focus into the popup; on error, onto the close button.
  useEffect(() => {
    if (state.status === "error") closeRef.current?.focus();
    else if (open) panelRef.current?.focus();
  }, [state.status, open]);

  useEffect(() => {
    if (state.status !== "error") return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [state.status, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="submit-overlay">
      <div
        ref={panelRef}
        className={`submit-panel is-${state.status}`}
        role={state.status === "error" ? "alertdialog" : "dialog"}
        aria-modal="true"
        aria-label="Saving your response"
        tabIndex={-1}
      >
        {state.status === "error" && (
          <button ref={closeRef} type="button" className="submit-close" aria-label="Close and go back to the form" onClick={onClose}>
            <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
              <path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        )}

        <div role="status" aria-live="polite" className="submit-body">
          {state.status === "saving" && (
            <>
              <span className="submit-spinner" aria-hidden="true" />
              <p className="submit-msg">Saving data...</p>
            </>
          )}
          {state.status === "success" && (
            <>
              <span className="submit-icon is-ok" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="34" height="34">
                  <path d="m5.5 12.5 4.2 4.2 8.8-9.4" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <p className="submit-msg">Data successfully saved!</p>
              <p className="submit-sub">Taking you to Monitoring…</p>
            </>
          )}
          {state.status === "error" && (
            <>
              <span className="submit-icon is-bad" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="34" height="34">
                  <path d="m6.5 6.5 11 11M17.5 6.5l-11 11" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" />
                </svg>
              </span>
              <p className="submit-msg">{state.message}</p>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
