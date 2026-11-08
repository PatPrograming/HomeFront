"""Gateway-held federal agency API handlers.

Keys stay in env vars; results never echo credentials (AML.M0026).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _env(*names: str) -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def _http_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: int = 25,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    hdrs = {
        "Accept": "application/json",
        "User-Agent": "HomeFront/0.1 (agency research; +https://github.com/PatPrograming/HomeFront)",
        **(headers or {}),
    }
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes).decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            return {
                "status": "error",
                "http_status": status,
                "error": "Non-JSON response",
                "raw_preview": raw[:600],
                "credentials_exposed": False,
            }
        return {
            "status": "ok",
            "simulated": False,
            "http_status": status,
            "data": payload,
            "credentials_exposed": False,
        }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            pass
        return {
            "status": "error",
            "http_status": exc.code,
            "error": f"HTTP {exc.code}: {exc.reason}",
            "detail_preview": body,
            "credentials_exposed": False,
        }
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"status": "error", "error": str(exc), "credentials_exposed": False}


def _need_key(key_name: str, key: str, signup: str) -> dict[str, Any] | None:
    if key:
        return None
    return {
        "status": "error",
        "error": f"missing_{key_name.lower()}",
        "hint": f"Set {key_name} in the repo-root .env ({signup}).",
        "credentials_exposed": False,
    }


def handle_census_query(args: dict[str, Any]) -> dict[str, Any]:
    """Census Data API (population, ACS, etc.)."""
    key = _env("CENSUS_API_KEY", "DATA_GOV_API_KEY")
    missing = _need_key("CENSUS_API_KEY", key, "https://api.census.gov/data/key_signup.html")
    if missing:
        return missing
    year = str(args.get("year") or "2023")
    dataset = (args.get("dataset") or "acs/acs1").strip().strip("/")
    get = (args.get("get") or "NAME,B01003_001E").strip()
    for_geo = (args.get("for") or "place:*").strip()
    in_geo = (args.get("in") or "").strip()
    params = {"get": get, "for": for_geo, "key": key}
    if in_geo:
        params["in"] = in_geo
    url = f"https://api.census.gov/data/{year}/{dataset}?{urllib.parse.urlencode(params)}"
    out = _http_json(url)
    out.update(
        {
            "source": "api.census.gov",
            "year": year,
            "dataset": dataset,
            "get": get,
            "for": for_geo,
            "in": in_geo or None,
            "atlas_note": "API key retained by gateway (AML.M0026).",
        }
    )
    # Compact preview: first header + up to 15 rows
    data = out.get("data")
    if isinstance(data, list) and data:
        out["rows_preview"] = data[:16]
        out.pop("data", None)
    return out


def handle_openfda_search(args: dict[str, Any]) -> dict[str, Any]:
    key = _env("OPENFDA_API_KEY", "DATA_GOV_API_KEY")
    missing = _need_key("OPENFDA_API_KEY", key, "https://open.fda.gov/apis/authentication/")
    if missing:
        return missing
    endpoint = (args.get("endpoint") or "drug/event").strip().strip("/")
    search = (args.get("search") or args.get("q") or "patient.drug.medicinalproduct:aspirin").strip()
    limit = max(1, min(int(args.get("limit") or 5), 20))
    params = {"api_key": key, "search": search, "limit": str(limit)}
    url = f"https://api.fda.gov/{endpoint}.json?{urllib.parse.urlencode(params)}"
    out = _http_json(url)
    out.update(
        {
            "source": "api.fda.gov",
            "endpoint": endpoint,
            "search": search,
            "atlas_note": "API key retained by gateway (AML.M0026).",
        }
    )
    data = out.get("data")
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            out["result_count"] = len(results)
            out["results_preview"] = results[:5]
            out.pop("data", None)
    return out


def handle_eia_query(args: dict[str, Any]) -> dict[str, Any]:
    key = _env("EIA_API_KEY", "NREL_API_KEY", "DATA_GOV_API_KEY")
    missing = _need_key("EIA_API_KEY", key, "https://www.eia.gov/opendata/")
    if missing:
        return missing
    route = (args.get("route") or "electricity/rto/region-data").strip().strip("/")
    length = max(1, min(int(args.get("length") or 5), 50))
    frequency = (args.get("frequency") or "monthly").strip()
    params = {
        "api_key": key,
        "frequency": frequency,
        "data[0]": "value",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "offset": "0",
        "length": str(length),
    }
    facet = (args.get("facet") or "").strip()
    if facet and ":" in facet:
        name, value = facet.split(":", 1)
        params[f"facets[{name}][]"] = value
    url = f"https://api.eia.gov/v2/{route}/data/?{urllib.parse.urlencode(params)}"
    out = _http_json(url)
    out.update(
        {
            "source": "api.eia.gov",
            "route": route,
            "atlas_note": "API key retained by gateway (AML.M0026).",
        }
    )
    data = out.get("data")
    if isinstance(data, dict):
        response = data.get("response") if isinstance(data.get("response"), dict) else data
        rows = response.get("data") if isinstance(response, dict) else None
        if isinstance(rows, list):
            out["result_count"] = len(rows)
            out["results_preview"] = rows[:10]
            out.pop("data", None)
    return out


def handle_nasa_search(args: dict[str, Any]) -> dict[str, Any]:
    key = _env("NASA_API_KEY")
    q = (args.get("q") or "earth").strip()
    # Images API is public; APOD needs a key — use images search primarily.
    params = {"q": q, "media_type": "image"}
    url = f"https://images-api.nasa.gov/search?{urllib.parse.urlencode(params)}"
    out = _http_json(url)
    out.update({"source": "images-api.nasa.gov", "query": q, "credentials_exposed": False})
    data = out.get("data")
    items = []
    if isinstance(data, dict):
        col = data.get("collection")
        if isinstance(col, dict) and isinstance(col.get("items"), list):
            for item in col["items"][:8]:
                if not isinstance(item, dict):
                    continue
                meta = (item.get("data") or [{}])[0] if item.get("data") else {}
                items.append(
                    {
                        "title": meta.get("title"),
                        "nasa_id": meta.get("nasa_id"),
                        "description": (meta.get("description") or "")[:240],
                        "date_created": meta.get("date_created"),
                    }
                )
    out["results"] = items
    out["result_count"] = len(items)
    out.pop("data", None)
    if key:
        out["nasa_key_configured"] = True  # APOD etc. available later; images don't need it
    return out


def handle_noaa_climate(args: dict[str, Any]) -> dict[str, Any]:
    token = _env("NOAA_CDO_TOKEN")
    missing = _need_key("NOAA_CDO_TOKEN", token, "https://www.ncdc.noaa.gov/cdo-web/token")
    if missing:
        return missing
    datasetid = (args.get("datasetid") or "GHCND").strip()
    locationid = (args.get("locationid") or "FIPS:06").strip()
    startdate = (args.get("startdate") or "2023-01-01").strip()
    enddate = (args.get("enddate") or "2023-01-07").strip()
    datatypeid = (args.get("datatypeid") or "").strip()
    params = {
        "datasetid": datasetid,
        "locationid": locationid,
        "startdate": startdate,
        "enddate": enddate,
        "limit": "25",
    }
    if datatypeid:
        params["datatypeid"] = datatypeid
    url = f"https://www.ncei.noaa.gov/cdo-web/api/v2/data?{urllib.parse.urlencode(params)}"
    out = _http_json(url, headers={"token": token})
    out.update(
        {
            "source": "ncei.noaa.gov CDO",
            "datasetid": datasetid,
            "locationid": locationid,
            "startdate": startdate,
            "enddate": enddate,
            "atlas_note": "Token retained by gateway (AML.M0026).",
        }
    )
    data = out.get("data")
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        out["result_count"] = len(data["results"])
        out["results_preview"] = data["results"][:15]
        out.pop("data", None)
    return out


def handle_nps_parks(args: dict[str, Any]) -> dict[str, Any]:
    key = _env("NPS_API_KEY")
    missing = _need_key("NPS_API_KEY", key, "https://www.nps.gov/subjects/developer/get-started.htm")
    if missing:
        return missing
    q = (args.get("q") or "").strip()
    park_code = (args.get("parkCode") or "").strip()
    params: dict[str, str] = {"api_key": key, "limit": "10"}
    if q:
        params["q"] = q
    if park_code:
        params["parkCode"] = park_code
    url = f"https://developer.nps.gov/api/v1/parks?{urllib.parse.urlencode(params)}"
    out = _http_json(url)
    out.update(
        {
            "source": "developer.nps.gov",
            "query": q or None,
            "parkCode": park_code or None,
            "atlas_note": "API key retained by gateway (AML.M0026).",
        }
    )
    data = out.get("data")
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        parks = []
        for p in data["data"][:10]:
            if not isinstance(p, dict):
                continue
            parks.append(
                {
                    "fullName": p.get("fullName"),
                    "parkCode": p.get("parkCode"),
                    "states": p.get("states"),
                    "description": (p.get("description") or "")[:240],
                    "url": p.get("url"),
                }
            )
        out["result_count"] = len(parks)
        out["results"] = parks
        out.pop("data", None)
    return out


def handle_bls_series(args: dict[str, Any]) -> dict[str, Any]:
    key = _env("BLS_API_KEY")
    missing = _need_key("BLS_API_KEY", key, "https://www.bls.gov/developers/home.htm")
    if missing:
        return missing
    series_id = (args.get("series_id") or "CUUR0000SA0").strip()
    startyear = str(args.get("startyear") or "2022")
    endyear = str(args.get("endyear") or "2023")
    payload = {
        "seriesid": [series_id],
        "startyear": startyear,
        "endyear": endyear,
        "registrationkey": key,
    }
    body = json.dumps(payload).encode("utf-8")
    out = _http_json(
        "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    out.update(
        {
            "source": "api.bls.gov",
            "series_id": series_id,
            "startyear": startyear,
            "endyear": endyear,
            "atlas_note": "Registration key retained by gateway (AML.M0026).",
        }
    )
    data = out.get("data")
    if isinstance(data, dict):
        results = data.get("Results", {}).get("series") if isinstance(data.get("Results"), dict) else None
        if isinstance(results, list) and results:
            series = results[0] if isinstance(results[0], dict) else {}
            points = series.get("data") if isinstance(series, dict) else []
            out["series_title"] = series.get("catalog", {}).get("series_title") if isinstance(series.get("catalog"), dict) else None
            out["observations_preview"] = (points or [])[:12]
            out.pop("data", None)
    return out


def handle_fec_search(args: dict[str, Any]) -> dict[str, Any]:
    key = _env("FEC_API_KEY", "DATA_GOV_API_KEY")
    missing = _need_key("FEC_API_KEY", key, "https://api.data.gov/signup/")
    if missing:
        return missing
    q = (args.get("q") or "").strip()
    params = {"api_key": key, "per_page": "10"}
    if q:
        params["q"] = q
    url = f"https://api.open.fec.gov/v1/candidates/search/?{urllib.parse.urlencode(params)}"
    out = _http_json(url)
    out.update(
        {
            "source": "api.open.fec.gov",
            "query": q or None,
            "atlas_note": "API key retained by gateway (AML.M0026).",
        }
    )
    data = out.get("data")
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        out["result_count"] = len(data["results"])
        out["results_preview"] = [
            {
                "name": r.get("name"),
                "party": r.get("party_full") or r.get("party"),
                "office": r.get("office_full") or r.get("office"),
                "state": r.get("state"),
                "candidate_id": r.get("candidate_id"),
            }
            for r in data["results"][:10]
            if isinstance(r, dict)
        ]
        out.pop("data", None)
    return out


def handle_epa_search(args: dict[str, Any]) -> dict[str, Any]:
    """EPA Envirofacts / facility lookup. Uses EPA_API_KEY when set (api.data.gov)."""
    key = _env("EPA_API_KEY", "DATA_GOV_API_KEY")
    q = (args.get("q") or args.get("city") or "WASHINGTON").strip()
    # Envirofacts FRS by city — public; attach api_key when available for rate limits.
    path = urllib.parse.quote(q.upper())
    url = f"https://data.epa.gov/efservice/frs_program_facility/CITY_NAME/{path}/JSON"
    if key:
        url = f"{url}?{urllib.parse.urlencode({'api_key': key})}"
    out = _http_json(url, max_bytes=300_000)
    out.update(
        {
            "source": "data.epa.gov Envirofacts",
            "query": q,
            "atlas_note": "API key retained by gateway when configured (AML.M0026).",
            "epa_key_configured": bool(key),
        }
    )
    data = out.get("data")
    rows: list[Any] = data if isinstance(data, list) else []
    if isinstance(data, dict):
        rows = data.get("results") or data.get("data") or []
        if not isinstance(rows, list):
            rows = []
    preview = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        preview.append(
            {
                "facility_name": row.get("FACILITY_NAME") or row.get("facility_name"),
                "city": row.get("CITY_NAME") or row.get("city_name"),
                "state": row.get("STATE_CODE") or row.get("state_code"),
                "program": row.get("PGM_SYS_ACRNM") or row.get("pgm_sys_acrnm"),
            }
        )
    out["result_count"] = len(preview)
    out["results_preview"] = preview
    out.pop("data", None)
    return out


def handle_fcc_search(args: dict[str, Any]) -> dict[str, Any]:
    """FCC license search. Uses FCC_API_KEY or DATA_GOV_API_KEY when required."""
    key = _env("FCC_API_KEY", "DATA_GOV_API_KEY")
    q = (args.get("q") or "").strip()
    if not key:
        missing = _need_key("FCC_API_KEY", key, "https://api.data.gov/signup/")
        if missing:
            return missing
    params: dict[str, str] = {"limit": "10"}
    if q:
        params["searchValue"] = q
    if key:
        params["api_key"] = key
    url = (
        "https://data.fcc.gov/api/license-view/basicSearch/getLicenses"
        f"?{urllib.parse.urlencode(params)}"
    )
    out = _http_json(url)
    out.update(
        {
            "source": "data.fcc.gov license-view",
            "query": q or None,
            "atlas_note": "API key retained by gateway (AML.M0026).",
        }
    )
    data = out.get("data")
    licenses: list[Any] = []
    if isinstance(data, dict):
        licenses = data.get("Licenses", {}).get("License") if isinstance(data.get("Licenses"), dict) else []
        if isinstance(licenses, dict):
            licenses = [licenses]
        if not isinstance(licenses, list):
            licenses = []
    out["result_count"] = len(licenses)
    out["results_preview"] = [
        {
            "licName": r.get("licName"),
            "frn": r.get("frn"),
            "callsign": r.get("callsign"),
            "serviceDesc": r.get("serviceDesc"),
            "statusDesc": r.get("statusDesc"),
        }
        for r in licenses[:10]
        if isinstance(r, dict)
    ]
    out.pop("data", None)
    return out
