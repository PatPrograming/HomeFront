from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .enricher import AnnotateRequest, AnnotateResponse, AtlasEnricher


def build_router(enricher: AtlasEnricher) -> APIRouter:
    router = APIRouter(prefix="/atlas", tags=["atlas"])

    @router.get("/meta")
    def meta() -> dict:
        return {
            "collection": enricher.pack.get("collection"),
            "counts": enricher.pack.get("counts"),
            "filter": enricher.pack.get("filter"),
        }

    @router.get("/techniques")
    def list_techniques(
        platform: str | None = Query(default="Agentic AI"),
    ) -> list[dict]:
        return enricher.list_techniques(platform=platform)

    @router.get("/techniques/{technique_id}")
    def get_technique(technique_id: str) -> dict:
        tech = enricher.get_technique(technique_id)
        if not tech:
            raise HTTPException(status_code=404, detail=f"Unknown technique {technique_id}")
        return {
            **tech,
            "mitigations": enricher.mitigations_for_technique(technique_id),
        }

    @router.get("/mitigations")
    def list_mitigations() -> list[dict]:
        return enricher.list_mitigations()

    @router.get("/mitigations/{mitigation_id}")
    def get_mitigation(mitigation_id: str) -> dict:
        mit = enricher.get_mitigation(mitigation_id)
        if not mit:
            raise HTTPException(status_code=404, detail=f"Unknown mitigation {mitigation_id}")
        mitigates = (enricher.relationships.get("mitigates") or {}).get(mitigation_id) or []
        return {**mit, "mitigates": mitigates}

    @router.post("/annotate", response_model=AnnotateResponse)
    def annotate(req: AnnotateRequest) -> AnnotateResponse:
        return enricher.annotate(req)

    return router
