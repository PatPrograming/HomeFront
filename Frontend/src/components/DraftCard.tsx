import { useState } from "react";
import { api, type Draft, type DraftPatch } from "../lib/api";
import { connectionMessage } from "../lib/errors";
import { DraftCardView } from "./DraftCardView";

// The draft card's state: whether it is being edited, the unsaved edits and the staffer's change request. Saving
// is a PATCH of the changed fields; a change request asks the agent for the next version. Both reload the drafts.
export default function DraftCard({ draft, onOpen, onChanged }: { draft: Draft; onOpen: (id: string) => void; onChanged: () => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<DraftPatch>({});
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, after: () => void) {
    setBusy(true);
    setError(null);
    try {
      await action();
      after();
      await onChanged();
    } catch (e) {
      setError(connectionMessage(e, false));
    } finally {
      setBusy(false);
    }
  }

  return (
    <DraftCardView
      draft={draft}
      editing={editing}
      edits={edits}
      instruction={instruction}
      busy={busy}
      error={error}
      onOpen={onOpen}
      onEdit={(on) => {
        setEditing(on);
        setEdits({});
      }}
      onChange={(patch) => setEdits({ ...edits, ...patch })}
      onSave={async (changes) => {
        if (!Object.keys(changes).length) return setEditing(false);
        await run(() => api.updateDraft(draft.id, changes), () => {
          setEditing(false);
          setEdits({});
        });
      }}
      onInstruction={setInstruction}
      onRevise={async (text) => {
        if (!text.trim()) return;
        await run(() => api.reviseDraft(draft.id, text.trim()), () => setInstruction(""));
      }}
    />
  );
}
