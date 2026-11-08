import "./DraftControls.css";

interface Props {
  savedAt: string | null;
  onSave: () => void;
  onDelete: () => void;
}

// "Delete response" / "Save response" row shown in every section of every form.
export default function DraftControls({ savedAt, onSave, onDelete }: Props) {
  return (
    <div className="draft-controls">
      <p className="draft-status" role="status" aria-live="polite">
        {savedAt
          ? `Response saved at ${new Date(savedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
          : ""}
      </p>
      <div className="draft-buttons">
        <button type="button" className="btn btn-outline btn-sm" onClick={onDelete}>
          Delete response
        </button>
        <button type="button" className="btn btn-primary btn-sm" onClick={onSave}>
          Save response
        </button>
      </div>
    </div>
  );
}
