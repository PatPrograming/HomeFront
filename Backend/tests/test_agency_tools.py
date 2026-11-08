"""Agency API tools are registered and handlers never echo secrets."""

import os

from mcp_tools.handlers import HANDLERS, invoke_tool
from mcp_tools.registry import TOOL_REGISTRY


AGENCY_TOOLS = [
    "census.query",
    "openfda.search",
    "eia.query",
    "nasa.search",
    "noaa.climate",
    "nps.parks",
    "bls.series",
    "fec.search",
    "epa.search",
    "fcc.search",
]


def test_agency_tools_registered_with_handlers():
    for name in AGENCY_TOOLS:
        assert name in TOOL_REGISTRY
        assert name in HANDLERS
        assert TOOL_REGISTRY[name].holds_secrets is True


def test_missing_key_does_not_expose_secret(monkeypatch):
    for key in (
        "CENSUS_API_KEY",
        "DATA_GOV_API_KEY",
        "OPENFDA_API_KEY",
        "EIA_API_KEY",
        "NREL_API_KEY",
        "NOAA_CDO_TOKEN",
        "NPS_API_KEY",
        "BLS_API_KEY",
        "FEC_API_KEY",
        "FCC_API_KEY",
        "EPA_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    out = invoke_tool("census.query", {"year": "2023", "dataset": "acs/acs1", "get": "NAME", "for": "state:06", "in": ""})
    assert out["status"] == "error"
    assert out.get("credentials_exposed") is False
    blob = str(out).lower()
    assert "api_key" not in blob or "missing" in blob


def test_nasa_search_works_without_key(monkeypatch):
    monkeypatch.delenv("NASA_API_KEY", raising=False)

    class _Resp:
        status = 200

        def read(self, _n):
            return b'{"collection":{"items":[{"data":[{"title":"Earth","nasa_id":"1","description":"x"}]}]}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    out = invoke_tool("nasa.search", {"q": "earth"})
    assert out["status"] == "ok"
    assert out["result_count"] >= 1
    assert out.get("credentials_exposed") is False
