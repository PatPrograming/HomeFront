"""Seed data that lane B loads into DynamoDB and the guardrail eval scores (check C1)."""

import json
import re
from collections import Counter

from conftest import DATA_DIR
from mandate_contracts.ddb import CaseItem, EmailItem

BENIGN = {"angry_benign", "pii_benign", "non_english_benign"}
ATTACK = {"obvious_attack", "disguised_staff_memo", "hidden_in_quote", "encoded_or_split"}


def load(name):
    return json.loads((DATA_DIR / name).read_text())


def test_25_emails_with_ids_and_contract_shape():
    emails = load("emails.json")
    assert [e["id"] for e in emails] == [f"email-{i}" for i in range(1, 26)]
    for e in emails:
        EmailItem.model_validate(e)  # raises if from/subject/body are missing


def test_every_category_is_present_and_labelled():
    emails = load("emails.json")
    cats = Counter(e["category"] for e in emails)
    assert set(cats) == BENIGN | ATTACK
    assert {e["lang"] for e in emails if e["category"] == "non_english_benign"} >= {"vi", "es"}


def test_one_email_carries_the_demo_injection():
    demo = [e for e in load("emails.json") if e.get("demo")]
    assert len(demo) == 1 and demo[0]["category"] in ATTACK


def test_cases_20_rep_x_and_5_rep_y():
    cases = load("cases.json")
    assert [c["id"] for c in cases] == [f"case-{i}" for i in range(1, 26)]
    offices = Counter(CaseItem.model_validate(c).office for c in cases)
    assert offices == {"rep-x": 20, "rep-y": 5}


def test_pii_is_fake():
    # Only obviously invalid SSNs (area 900-999 is never issued) and 555-01xx phone numbers.
    text = (DATA_DIR / "emails.json").read_text() + (DATA_DIR / "cases.json").read_text()
    for area in re.findall(r"\b(\d{3})-\d{2}-\d{4}\b", text):
        assert int(area) >= 900
    for phone in re.findall(r"\(?\d{3}\)?[ -]\d{3}-\d{4}", text):
        assert "555-01" in phone
