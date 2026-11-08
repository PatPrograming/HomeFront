"""Topic → research tool selection."""

from identity_gateway.topic_router import (
    AGENCY_RESEARCH_TOOLS,
    select_research_tools,
    topic_routing_note,
)


def test_population_selects_census():
    tools, topics = select_research_tools(
        "What is the population of Santa Clara city?",
        AGENCY_RESEARCH_TOOLS,
    )
    assert "demographics" in topics
    assert tools == ["census.query", "datagov.search", "web.scrape"]


def test_drug_selects_openfda():
    tools, topics = select_research_tools("Find adverse events for aspirin", AGENCY_RESEARCH_TOOLS)
    assert "health_drugs" in topics
    assert "openfda.search" in tools
    assert "census.query" not in tools


def test_no_match_offers_all_granted():
    tools, topics = select_research_tools("Summarize general civic info", AGENCY_RESEARCH_TOOLS)
    assert topics == []
    assert set(tools) == set(AGENCY_RESEARCH_TOOLS)


def test_intersects_grants():
    tools, topics = select_research_tools(
        "CPI inflation and unemployment",
        {"bls.series", "datagov.search"},
    )
    assert "labor" in topics
    assert tools == ["bls.series", "datagov.search"]


def test_routing_note_mentions_topics():
    note = topic_routing_note(["demographics"], ["census.query", "web.scrape"])
    assert "demographics" in note
    assert "census.query" in note
