from scripts.cleanup_orphaned_app_reflections import parse_reflection_ids


def test_parse_reflection_ids_skips_comments_and_dupes() -> None:
    text = """
# header
ref-1
ref-2
ref-1

ref-3
"""
    assert parse_reflection_ids(text) == ["ref-1", "ref-2", "ref-3"]


def test_parse_reflection_ids_empty() -> None:
    assert parse_reflection_ids("\n# only comments\n") == []
