#!/usr/bin/env python3
"""Build a compact Agentic AI ATLAS pack from the official atlas-data YAML.

Pulls (or reads a local) ATLAS export, keeps techniques tagged with the
Agentic AI platform plus related mitigations/relationships, and writes
atlas-agentic-pack.json for offline use by the enricher.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# ATLAS-latest.yaml in the repo is a pointer; pin a concrete release.
DEFAULT_URL = (
    "https://raw.githubusercontent.com/mitre-atlas/atlas-data/main/dist/v6/ATLAS-2026.10.yaml"
)
PINNED_VERSION = "2026.10"
PLATFORM = "Agentic AI"

# Always retain these IDs even if platform tagging differs across releases.
SEED_TECHNIQUE_IDS = {
    "AML.T0051",
    "AML.T0051.000",
    "AML.T0051.001",
    "AML.T0051.002",
    "AML.T0053",
    "AML.T0080",
    "AML.T0081",
    "AML.T0083",
    "AML.T0084",
    "AML.T0084.001",
    "AML.T0084.002",
    "AML.T0086",
    "AML.T0098",
    "AML.T0101",
    "AML.T0110",
}
SEED_MITIGATION_IDS = {
    "AML.M0024",
    "AML.M0026",
    "AML.M0028",
    "AML.M0029",
    "AML.M0030",
    "AML.M0033",
}


def load_yaml(source: str) -> dict:
    if source.startswith("http://") or source.startswith("https://"):
        print(f"Downloading {source} ...")
        with urllib.request.urlopen(source, timeout=120) as resp:
            raw = resp.read()
        return yaml.safe_load(raw)
    path = Path(source)
    print(f"Reading {path} ...")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def technique_is_agentic(tech: dict) -> bool:
    platforms = tech.get("platforms") or []
    return PLATFORM in platforms


def build_pack(data: dict) -> dict:
    techniques_src = data.get("techniques") or {}
    mitigations_src = data.get("mitigations") or {}
    relationships_src = data.get("relationships") or {}
    tactics_src = data.get("tactics") or {}

    selected_techniques: dict[str, dict] = {}
    for tid, tech in techniques_src.items():
        if technique_is_agentic(tech) or tid in SEED_TECHNIQUE_IDS:
            selected_techniques[tid] = {
                "id": tech.get("id", tid),
                "name": tech.get("name"),
                "description": tech.get("description"),
                "platforms": tech.get("platforms") or [],
                "maturity": tech.get("maturity"),
                "object-type": tech.get("object-type", "technique"),
            }

    # Include parents of selected sub-techniques.
    for tid in list(selected_techniques):
        if "." in tid.split("T")[-1] if "T" in tid else False:
            parent = tid.rsplit(".", 1)[0]
            if parent in techniques_src and parent not in selected_techniques:
                tech = techniques_src[parent]
                selected_techniques[parent] = {
                    "id": tech.get("id", parent),
                    "name": tech.get("name"),
                    "description": tech.get("description"),
                    "platforms": tech.get("platforms") or [],
                    "maturity": tech.get("maturity"),
                    "object-type": tech.get("object-type", "technique"),
                }

    # Mitigations that mitigate selected techniques, plus seed mitigations.
    selected_mitigations: dict[str, dict] = {}
    mitigates_map: dict[str, list[str]] = {}

    for mid, rels in relationships_src.items():
        if not isinstance(rels, dict):
            continue
        mitigates = rels.get("mitigates") or []
        targets = []
        for edge in mitigates:
            target = edge.get("target") if isinstance(edge, dict) else None
            if target in selected_techniques:
                targets.append(target)
        if targets or mid in SEED_MITIGATION_IDS:
            if mid in mitigations_src:
                m = mitigations_src[mid]
                selected_mitigations[mid] = {
                    "id": m.get("id", mid),
                    "name": m.get("name"),
                    "description": m.get("description"),
                    "categories": m.get("categories") or [],
                    "object-type": m.get("object-type", "mitigation"),
                }
                mitigates_map[mid] = sorted(set(targets))

    for mid in SEED_MITIGATION_IDS:
        if mid in mitigations_src and mid not in selected_mitigations:
            m = mitigations_src[mid]
            selected_mitigations[mid] = {
                "id": m.get("id", mid),
                "name": m.get("name"),
                "description": m.get("description"),
                "categories": m.get("categories") or [],
                "object-type": m.get("object-type", "mitigation"),
            }
            mitigates_map.setdefault(mid, [])

    # Fill mitigates from seed mitigations against selected techniques.
    for mid, rels in relationships_src.items():
        if mid not in selected_mitigations or not isinstance(rels, dict):
            continue
        for edge in rels.get("mitigates") or []:
            target = edge.get("target") if isinstance(edge, dict) else None
            if target in selected_techniques:
                mitigates_map.setdefault(mid, [])
                if target not in mitigates_map[mid]:
                    mitigates_map[mid].append(target)

    # Tactic links for selected techniques.
    achieves_map: dict[str, list[str]] = {}
    selected_tactics: dict[str, dict] = {}
    for tid in selected_techniques:
        rels = relationships_src.get(tid) or {}
        if not isinstance(rels, dict):
            continue
        for edge in rels.get("achieves") or []:
            target = edge.get("target") if isinstance(edge, dict) else None
            if not target:
                continue
            achieves_map.setdefault(tid, []).append(target)
            if target in tactics_src and target not in selected_tactics:
                t = tactics_src[target]
                selected_tactics[target] = {
                    "id": t.get("id", target),
                    "name": t.get("name"),
                    "description": t.get("description"),
                    "object-type": t.get("object-type", "tactic"),
                }

    collection = data.get("collection") or {}
    return {
        "format-version": data.get("format-version"),
        "source": "mitre-atlas/atlas-data",
        "filter": {"platforms": [PLATFORM], "seed_techniques": sorted(SEED_TECHNIQUE_IDS)},
        "collection": {
            "id": collection.get("id"),
            "version": collection.get("version"),
            "name": collection.get("name"),
        },
        "tactics": selected_tactics,
        "techniques": selected_techniques,
        "mitigations": selected_mitigations,
        "relationships": {
            "mitigates": mitigates_map,
            "achieves": achieves_map,
        },
        "counts": {
            "tactics": len(selected_tactics),
            "techniques": len(selected_techniques),
            "mitigations": len(selected_mitigations),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=DEFAULT_URL,
        help="URL or local path to ATLAS YAML (default: ATLAS-latest.yaml)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "atlas-agentic-pack.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    data = load_yaml(args.source)
    pack = build_pack(data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(pack, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(
        f"Wrote {args.out} "
        f"(v{pack['collection'].get('version')}: "
        f"{pack['counts']['techniques']} techniques, "
        f"{pack['counts']['mitigations']} mitigations)"
    )


if __name__ == "__main__":
    main()
