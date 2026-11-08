/** ATLAS technique / mitigation chips with optional names from the demo matrix. */

export interface AtlasEntry {
  id: string;
  name?: string | null;
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
  const techName = (id: string) => techniques.find((t) => t.id === id)?.name;
  const mitName = (id: string) => mitigations.find((m) => m.id === id)?.name;
  return (
    <div className={`id-row${compact ? " compact" : ""}`}>
      {techniqueIds.map((id) => (
        <code key={id} className="tech" title={techName(id) || id}>
          {id}
          {!compact && techName(id) ? ` · ${techName(id)}` : ""}
        </code>
      ))}
      {mitigationIds.map((id) => (
        <code key={id} className="mit" title={mitName(id) || id}>
          {id}
          {!compact && mitName(id) ? ` · ${mitName(id)}` : ""}
        </code>
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
