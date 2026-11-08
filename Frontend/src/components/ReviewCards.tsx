import type { ChatMessage, Draft } from "../lib/api";
import DraftCard from "./DraftCard";

// Which reply each draft belongs under: the first assistant reply at or after the draft was made. A draft newer
// than every reply belongs to the turn still running, so it waits for that turn's reply instead of going under an
// earlier one; once the turn is over it goes under the latest reply (the server's clock a little ahead of the
// chat's). The "N waiting" badge reaches every draft meanwhile.
export function draftsByReply(messages: ChatMessage[], drafts: Draft[]): Map<string, Draft[]> {
  const replies = messages.filter((m) => m.role === "assistant");
  const running = messages[messages.length - 1]?.role === "user"; // its reply isn't here yet
  const placed = new Map<string, Draft[]>();
  if (!replies.length) return placed;
  for (const d of drafts) {
    const t = Date.parse(d.createdAt);
    const reply = replies.find((m) => Date.parse(m.ts) >= t) ?? (running ? undefined : replies[replies.length - 1]);
    if (reply) placed.set(reply.id, [...(placed.get(reply.id) ?? []), d]);
  }
  return placed;
}

// The drafts one reply produced, each as an editable draft card (DraftCard.tsx), like attachments to the reply.
// No hooks here, so a test can call it and check what each card is given.
export default function ReviewCards({ drafts, onOpen, onChanged }: { drafts: Draft[]; onOpen: (id: string) => void; onChanged: () => Promise<void> }) {
  return (
    <ul className="review-cards">
      {drafts.map((d) => (
        <li key={d.id}>
          <DraftCard draft={d} onOpen={onOpen} onChanged={onChanged} />
        </li>
      ))}
    </ul>
  );
}
