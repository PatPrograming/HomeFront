# ATLAS Agentic Pack

Vendors a filtered subset of [MITRE ATLAS](https://atlas.mitre.org/) data
(Agentic AI platform + seed technique/mitigation IDs) for offline use by
HomeFront's enricher and identity gateway.

## Rebuild

```bash
pip install -r requirements.txt
python scripts/build_agentic_pack.py
```

Default source is the pinned release
`dist/v6/ATLAS-2026.10.yaml` (the repo’s `ATLAS-latest.yaml` is only a pointer).

Pin a local YAML file:

```bash
python scripts/build_agentic_pack.py --source /path/to/ATLAS-2026.10.yaml
```

Output: `data/atlas-agentic-pack.json`
