from atlas_enricher import AnnotateRequest, AtlasEnricher
from atlas_enricher.enricher import AnnotateEvent


def test_pack_loads_agentic_core_ids():
    e = AtlasEnricher()
    assert e.version
    assert "AML.T0053" in e.techniques
    assert "AML.M0028" in e.mitigations


def test_annotate_deny_maps_to_tool_invocation():
    e = AtlasEnricher()
    res = e.annotate(
        AnnotateRequest(event=AnnotateEvent.deny, tool="email.send", agent_id="a1")
    )
    assert "AML.T0053" in res.technique_ids
    assert "AML.M0028" in res.mitigation_ids


def test_annotate_allow_email_includes_exfil_residual():
    e = AtlasEnricher()
    res = e.annotate(
        AnnotateRequest(event=AnnotateEvent.allow, tool="email.send", agent_id="a1")
    )
    assert "AML.T0086" in res.technique_ids
    assert "AML.M0029" in res.mitigation_ids
