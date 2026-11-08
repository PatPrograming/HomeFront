"""Select which research tools to offer for a task (keyword topic router).

The model still chooses among the offered tools; this narrows the menu so a
population question gets Census first, not NPS/NASA noise.
"""

from __future__ import annotations

from dataclasses import dataclass

# Always available for research (catalog + scrape fallback).
BASE_RESEARCH_TOOLS: frozenset[str] = frozenset({"datagov.search", "web.scrape"})

# All agency-backed research tools (minus email/social).
AGENCY_RESEARCH_TOOLS: frozenset[str] = frozenset(
    {
        "datagov.search",
        "web.scrape",
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
    }
)


@dataclass(frozen=True)
class TopicRule:
    name: str
    keywords: tuple[str, ...]
    tools: frozenset[str]


# First matching rules accumulate (union). Order only affects topic label order.
TOPIC_RULES: tuple[TopicRule, ...] = (
    TopicRule(
        "demographics",
        (
            "population",
            "census",
            "demograph",
            "acs ",
            "residents",
            "inhabitants",
            "city of",
            "county population",
            "how many people",
            "place population",
        ),
        frozenset({"census.query", "datagov.search", "web.scrape"}),
    ),
    TopicRule(
        "health_drugs",
        (
            "fda",
            "drug",
            "medication",
            "adverse",
            "recall",
            "pharma",
            "vaccine",
            "device adverse",
            "openfda",
        ),
        frozenset({"openfda.search", "datagov.search", "web.scrape"}),
    ),
    TopicRule(
        "energy",
        (
            "energy",
            "electric",
            "petroleum",
            "oil ",
            "natural gas",
            "eia",
            "power plant",
            "fuel",
            "megawatt",
            "nrel",
        ),
        frozenset({"eia.query", "datagov.search", "web.scrape"}),
    ),
    TopicRule(
        "climate_weather",
        (
            "climate",
            "weather",
            "temperature",
            "precipitation",
            "rainfall",
            "hurricane",
            "noaa",
            "snowfall",
            "drought",
        ),
        frozenset({"noaa.climate", "datagov.search", "web.scrape"}),
    ),
    TopicRule(
        "space",
        ("nasa", "mars", "astronaut", "satellite imagery", "hubble", "perseverance", "space "),
        frozenset({"nasa.search", "datagov.search", "web.scrape"}),
    ),
    TopicRule(
        "parks",
        ("national park", "nps ", "yosemite", "yellowstone", "park service", "trailhead"),
        frozenset({"nps.parks", "datagov.search", "web.scrape"}),
    ),
    TopicRule(
        "labor",
        (
            "unemployment",
            "cpi",
            "inflation",
            "labor",
            "bls",
            "wage",
            "jobs report",
            "employment",
            "consumer price",
        ),
        frozenset({"bls.series", "datagov.search", "web.scrape"}),
    ),
    TopicRule(
        "elections",
        ("fec", "campaign finance", "candidate", "election", "pac ", "political contribution"),
        frozenset({"fec.search", "datagov.search", "web.scrape"}),
    ),
    TopicRule(
        "environment",
        (
            "epa",
            "pollution",
            "superfund",
            "toxic",
            "emission",
            "envirofacts",
            "hazardous",
            "air quality",
            "cleanup site",
        ),
        frozenset({"epa.search", "datagov.search", "web.scrape"}),
    ),
    TopicRule(
        "telecom",
        ("fcc", "broadcast", "callsign", "spectrum", "telecom", "radio license", "broadband license"),
        frozenset({"fcc.search", "datagov.search", "web.scrape"}),
    ),
)


def select_research_tools(task: str, granted: set[str] | frozenset[str]) -> tuple[list[str], list[str]]:
    """Return (sorted tool names to offer, matched topic names).

    Intersection with `granted` is applied. If no topic matches, offer every
    granted agency research tool (model picks). Baseline catalog/scrape always
    stay if granted.
    """
    text = f" {task.lower()} "
    selected: set[str] = set(BASE_RESEARCH_TOOLS)
    topics: list[str] = []
    for rule in TOPIC_RULES:
        if any(kw.lower() in text for kw in rule.keywords):
            selected |= set(rule.tools)
            topics.append(rule.name)

    if not topics:
        selected = set(AGENCY_RESEARCH_TOOLS)

    offered = sorted(selected & set(granted))
    # Never return empty if the agent has grants — fall back to granted research tools.
    if not offered:
        offered = sorted(set(granted) & AGENCY_RESEARCH_TOOLS) or sorted(granted)
    return offered, topics


def topic_routing_note(topics: list[str], tools: list[str]) -> str:
    if topics:
        return (
            f"Topic router matched: {', '.join(topics)}. "
            f"Preferred tools for this task: {', '.join(tools)}. "
            "Use the agency-specific tool first when it fits; fall back to "
            "datagov.search or web.scrape only if needed."
        )
    return (
        "Topic router found no strong match; all research agency tools are available. "
        f"Tools: {', '.join(tools)}. Pick the agency API that best fits the question."
    )
