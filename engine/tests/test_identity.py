"""CanonicalEventResolver: aliases multilíngues, códigos FIFA, inversão de mando e recusa de fuzzy."""

from datetime import datetime, timedelta

import pytest

from edgefut.normalization.identity import (
    CanonicalEventResolver,
    EventIdentity,
    canonical_event_id,
    canonical_team_key,
    slugify,
)
from edgefut.normalization.teams import TeamMatch

KICKOFF = datetime(2026, 9, 23, 14, 30)


def _identity(source, sid, home, away, kickoff=KICKOFF, competition="Gulf Cup", dataset="INTL"):
    return EventIdentity(
        source=source, source_event_id=sid, kickoff_utc=kickoff,
        home=canonical_team_key(home, is_national=True), away=canonical_team_key(away, is_national=True),
        competition=competition, dataset_code=dataset,
    )


@pytest.mark.parametrize("alias", ["Iraq", "Iraque", "IRQ", "iraque", "Iraque (F)"])
def test_iraq_aliases_resolve_to_same_canonical(alias):
    m = canonical_team_key(alias, is_national=True)
    assert m.canonical == "Iraq" and m.method == "curated"


@pytest.mark.parametrize("alias", ["Oman", "Omã", "OMA", "oma"])
def test_oman_aliases_resolve_to_same_canonical(alias):
    assert canonical_team_key(alias, is_national=True).canonical == "Oman"


def test_canonical_event_id_format():
    cid = canonical_event_id(KICKOFF, "Gulf Cup", "Iraq", "Oman")
    assert cid == "20260923-gulf-cup-iraq-oman"
    assert slugify("Copa do Golfo — Sub-23") == "copa-do-golfo-sub-23"


def test_same_event_across_languages_and_codes():
    r = CanonicalEventResolver()
    a = _identity("superbet", "14888100", "Iraque", "Omã")
    b = _identity("international_results", "x", "IRQ", "OMA", kickoff=KICKOFF + timedelta(hours=2))
    m = r.same_event(a, b)
    assert m is not None and not m.swapped
    assert m.canonical_id == "20260923-intl-iraq-oman"
    assert a.canonical_id == b.canonical_id
    assert "competition" in m.method


def test_swapped_listing_is_detected_not_silently_merged():
    r = CanonicalEventResolver()
    a = _identity("superbet", "1", "Iraque", "Omã")
    b = _identity("dataset", "2", "Oman", "Iraq")
    m = r.same_event(a, b)
    assert m is not None and m.swapped is True
    assert m.confidence < r.same_event(a, _identity("dataset", "3", "Iraq", "Oman")).confidence


def test_kickoff_outside_tolerance_is_a_different_event():
    r = CanonicalEventResolver(kickoff_tolerance=timedelta(hours=3))
    a = _identity("superbet", "1", "Iraque", "Omã")
    b = _identity("dataset", "2", "Iraq", "Oman", kickoff=KICKOFF + timedelta(hours=26))
    assert r.same_event(a, b) is None


def test_different_dataset_never_merges():
    r = CanonicalEventResolver()
    a = _identity("superbet", "1", "Iraque", "Omã", dataset="INTL")
    b = _identity("dataset", "2", "Iraq", "Oman", dataset="E0")
    assert r.same_event(a, b) is None


def test_fuzzy_match_is_never_enough():
    r = CanonicalEventResolver()
    fuzzy_home = TeamMatch("Irak", "Iraq", "INTL", 0.9, "fuzzy", True)
    a = EventIdentity("superbet", "1", KICKOFF, fuzzy_home, canonical_team_key("Omã", is_national=True), "Gulf Cup", "INTL")
    b = _identity("dataset", "2", "Iraq", "Oman")
    assert a.resolved and not a.strong
    assert r.same_event(a, b) is None


def test_find_picks_best_and_ignores_itself():
    r = CanonicalEventResolver()
    a = _identity("superbet", "1", "Iraque", "Omã")
    same_source_same_id = _identity("superbet", "1", "Iraq", "Oman")
    later = _identity("dataset", "9", "Iraq", "Oman", kickoff=KICKOFF + timedelta(hours=2, minutes=30))
    exact = _identity("dataset", "8", "Iraq", "Oman")
    m = r.find(a, [same_source_same_id, later, exact])
    assert m is not None and m.b.source_event_id == "8"


def test_unresolved_team_has_no_canonical_id():
    a = EventIdentity("superbet", "1", KICKOFF, canonical_team_key("Time Fantasma FC", is_national=True), canonical_team_key("Omã", is_national=True))
    assert a.canonical_id is None and not a.resolved
