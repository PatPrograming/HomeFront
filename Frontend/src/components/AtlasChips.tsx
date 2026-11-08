/** ATLAS technique / mitigation chips with name + full description on hover. */

import "./AtlasChips.css";

export interface AtlasEntry {
  id: string;
  name?: string | null;
  description?: string | null;
}

function lookup(entries: AtlasEntry[], id: string): AtlasEntry | undefined {
  return entries.find((e) => e.id === id);
}

/** Plain-text title fallback (and screen-reader summary). */
export function atlasHoverText(entry: AtlasEntry | undefined, id: string): string {
  const name = entry?.name?.trim() || "";
  const description = entry?.description?.trim() || "";
  if (name && description) return `${name}\n\n${description}`;
  return description || name || id;
}

function Chip({
  id,
  kind,
  entry,
  compact,
}: {
  id: string;
  kind: "tech" | "mit";
  entry?: AtlasEntry;
  compact: boolean;
}) {
  const name = entry?.name?.trim() || "";
  const description = entry?.description?.trim() || "";
  const label = kind === "tech" ? "ATLAS technique" : "ATLAS mitigation";
  const tip = atlasHoverText(entry, id);
  return (
    <span className={`atlas-chip atlas-${kind}`} tabIndex={0}>
      <code className={kind} title={tip}>
        {id}
        {!compact && name ? ` · ${name}` : ""}
      </code>
      {(name || description) && (
        <span className="atlas-tip" role="tooltip">
          <span className="atlas-tip-kind">{label}</span>
          <strong className="atlas-tip-title">{name || id}</strong>
          {description && <span className="atlas-tip-body">{description}</span>}
        </span>
      )}
    </span>
  );
}

export function AtlasChips({
  techniqueIds = [],
  mitigationIds = [],
  techniques = [],
  mitigations = [],
  compact = false,
}: {
  techniqueIds?: string[];
  mitigationIds?: string[];
  techniques?: AtlasEntry[];
  mitigations?: AtlasEntry[];
  compact?: boolean;
}) {
  if (!techniqueIds.length && !mitigationIds.length) return null;
  return (
    <div className={`id-row atlas-chips${compact ? " compact" : ""}`}>
      {techniqueIds.map((id) => (
        <Chip key={id} id={id} kind="tech" entry={lookup(techniques, id)} compact={compact} />
      ))}
      {mitigationIds.map((id) => (
        <Chip key={id} id={id} kind="mit" entry={lookup(mitigations, id)} compact={compact} />
      ))}
    </div>
  );
}

/** Collect unique ATLAS IDs from a set of audit/flash outcomes for the contextual strip. */
export function collectAtlasIds(
  events: { atlas?: { technique_ids?: string[]; mitigation_ids?: string[] } | null }[],
): { techniqueIds: string[]; mitigationIds: string[] } {
  const techniques = new Set<string>();
  const mitigations = new Set<string>();
  for (const ev of events) {
    for (const id of ev.atlas?.technique_ids || []) techniques.add(id);
    for (const id of ev.atlas?.mitigation_ids || []) mitigations.add(id);
  }
  return { techniqueIds: [...techniques], mitigationIds: [...mitigations] };
}
