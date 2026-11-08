"""web.scrape helpers: Census key injection and CSV focus extraction."""

from identity_gateway import app as _gateway  # noqa: F401 — path setup via app.main
from mcp_tools.handlers import _focus_text, _with_census_api_key


def test_census_key_injected(monkeypatch):
    monkeypatch.setenv("DATA_GOV_API_KEY", "demo-key")
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    url = "https://api.census.gov/data/2023/acs/acs1?get=NAME,B01003_001E&for=place:69084&in=state:06"
    out = _with_census_api_key(url)
    assert "key=demo-key" in out
    assert "place%3A69084" in out or "place:69084" in out



def test_census_key_not_duplicated(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", "abc")
    url = "https://api.census.gov/data/2023/pep/charv?get=NAME,POP&for=us:*&key=already"
    assert _with_census_api_key(url) == url


def test_non_census_url_unchanged(monkeypatch):
    monkeypatch.setenv("DATA_GOV_API_KEY", "demo-key")
    url = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/cities/totals/sub-est2023_6.csv"
    assert _with_census_api_key(url) == url


def test_csv_focus_keeps_matching_rows():
    csv = (
        "SUMLEV,STATE,PLACE,NAME,POPESTIMATE2023\n"
        "040,06,00000,California,38965193\n"
        "162,06,00535,Alameda city,78280\n"
        "162,06,69084,Santa Clara city,130104\n"
        "162,06,70000,San Jose city,969655\n"
    )
    preview, meta = _focus_text(csv, "Santa Clara city", csv=True)
    assert meta["focused"] is True
    assert meta["match_count"] >= 1
    assert "Santa Clara city" in preview
    assert "130104" in preview
    assert "Alameda city" not in preview
