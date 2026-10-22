"""Regressions for fact safety: distinct facts survive, corrections are explicit."""

from __future__ import annotations

import pytest

from hermitcrab.agent.memory import MemoryCategory, MemoryStore


def _fact_contents(store: MemoryStore) -> list[str]:
    return sorted(item.content for item in store.read_memory(MemoryCategory.FACTS))


def test_reversed_relationship_creates_two_facts(tmp_path) -> None:
    store = MemoryStore(tmp_path)

    store.write_fact("Alice lends books", "Alice lends books to Bob.")
    store.write_fact("Bob lends books", "Bob lends books to Alice.")

    assert _fact_contents(store) == [
        "Alice lends books to Bob.",
        "Bob lends books to Alice.",
    ]


def test_negation_is_not_treated_as_a_duplicate(tmp_path) -> None:
    store = MemoryStore(tmp_path)

    store.write_fact("Alice and coffee", "Alice likes coffee.")
    store.write_fact("Alice and coffee now", "Alice does not like coffee.")

    assert len(store.read_memory(MemoryCategory.FACTS)) == 2


def test_same_predicate_with_distinct_people_is_preserved(tmp_path) -> None:
    store = MemoryStore(tmp_path)

    store.write_fact("Alice manages Bob", "Alice manages Bob.")
    store.write_fact("Alice manages Carol", "Alice manages Carol.")

    assert len(store.read_memory(MemoryCategory.FACTS)) == 2


def test_exact_duplicate_is_reused_without_a_second_file(tmp_path) -> None:
    store = MemoryStore(tmp_path)

    first = store.write_fact("Team standup", "Standup is at 09:30 on weekdays.")
    second = store.write_fact("Team standup", "Standup is at 09:30 on weekdays.  ")

    assert second.id == first.id
    assert second.metadata["_write_action"] == "reused_existing"
    assert len(store.read_memory(MemoryCategory.FACTS)) == 1


def test_correction_requires_an_explicit_target_and_preserves_other_facts(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    target = store.write_fact("Alice lends books", "Alice lends books to Bob.")
    unrelated = store.write_fact("Bob's birthday", "Bob's birthday is in May.")

    corrected = store.write_fact(
        "Alice lends books",
        "Alice lends books to Carol.",
        corrects=target.id,
    )

    assert corrected.id == target.id
    assert corrected.metadata["_write_action"] == "updated_existing"
    assert corrected.metadata["corrects"] == target.id
    items = store.read_memory(MemoryCategory.FACTS)
    assert len(items) == 2
    contents = {item.content for item in items}
    assert contents == {"Alice lends books to Carol.", "Bob's birthday is in May."}
    assert any(item.id == unrelated.id for item in items)
    assert corrected.metadata["_file_path"] == target.metadata["_file_path"]


def test_correction_by_exact_title_is_supported(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.write_fact("Alice lends books", "Alice lends books to Bob.")

    corrected = store.write_fact(
        "Alice lends books",
        "Alice lends books to Carol.",
        corrects="Alice lends books",
    )

    assert corrected.content == "Alice lends books to Carol."
    assert len(store.read_memory(MemoryCategory.FACTS)) == 1


def test_unknown_correction_target_raises_instead_of_overwriting(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.write_fact("Alice lends books", "Alice lends books to Bob.")

    with pytest.raises(ValueError, match="No fact matched correction target"):
        store.write_fact("Alice lends books", "Alice lends books to Carol.", corrects="missing-id")

    assert _fact_contents(store) == ["Alice lends books to Bob."]


def test_fact_confidence_survives_a_write_read_round_trip(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.write_fact(
        "User timezone",
        "The user lives in Europe/Lisbon.",
        confidence=0.8,
        source="user statement",
    )

    reloaded = MemoryStore(tmp_path)
    [item] = reloaded.read_memory(MemoryCategory.FACTS)

    assert item.metadata["confidence"] == pytest.approx(0.8)
    assert item.metadata["source"] == "user statement"


def test_fact_confidence_is_clamped_on_write(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.write_fact("Odd confidence", "An overconfident fact.", confidence=3.5)

    [item] = MemoryStore(tmp_path).read_memory(MemoryCategory.FACTS)

    assert item.metadata["confidence"] == 1.0
