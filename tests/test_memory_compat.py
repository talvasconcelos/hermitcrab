from pathlib import Path

from hermitcrab.agent.memory import MemoryStore


def test_memory_store_loads_legacy_file_without_id(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory" / "facts"
    memory_dir.mkdir(parents=True)
    legacy_file = memory_dir / "legacy.md"
    legacy_file.write_text(
        "---\n"
        "title: Legacy fact\n"
        "created_at: 2026-03-01T12-00-00\n"
        "updated_at: 2026-03-01T12-00-00\n"
        "type: facts\n"
        "tags: [legacy]\n"
        "---\n"
        "This memory predates explicit ids.\n",
        encoding="utf-8",
    )

    store = MemoryStore(tmp_path)
    results = store.read_memory("facts")

    assert len(results) == 1
    assert results[0].title == "Legacy fact"
    assert results[0].id
