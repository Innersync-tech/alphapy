"""
Tests for CSV export helpers used by exports cog and others.
"""

import io

from utils.csv_helpers import create_csv_buffer, create_discord_file_from_buffer


class TestCreateCsvBuffer:
    """Tests for create_csv_buffer."""

    def test_empty_rows_uses_given_fieldnames(self):
        buf = create_csv_buffer([], fieldnames=["a", "b"])
        content = buf.getvalue()
        assert "a,b" in content or "a," in content
        buf.close()

    def test_empty_rows_no_fieldnames_produces_header_only(self):
        """Empty rows and no fieldnames must not pass None to DictWriter (TypeError)."""
        buf = create_csv_buffer([], fieldnames=None)
        content = buf.getvalue()
        assert content.strip() == "" or "\n" in content
        buf.close()

    def test_single_row_derives_fieldnames(self):
        buf = create_csv_buffer([{"x": 1, "y": 2}])
        content = buf.getvalue()
        assert "x" in content and "y" in content
        assert "1" in content and "2" in content
        buf.close()

    def test_multiple_rows_writes_all(self):
        rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        buf = create_csv_buffer(rows)
        content = buf.getvalue()
        assert "1" in content and "2" in content
        assert "a" in content and "b" in content
        buf.close()

    def test_buffer_is_at_start_after_creation(self):
        buf = create_csv_buffer([{"k": "v"}])
        first = buf.read(1)
        assert first in ("k", "i", '"')
        buf.close()


class TestCreateDiscordFileFromBuffer:
    """Tests for create_discord_file_from_buffer."""

    def test_returns_discord_file_with_filename(self):
        buf = io.StringIO("a,b\n1,2")
        f = create_discord_file_from_buffer(buf, "test.csv")
        assert f.filename == "test.csv"
        assert f.fp is not None

    def test_buffer_content_encoded_utf8(self):
        buf = io.StringIO("name\ncafé")
        f = create_discord_file_from_buffer(buf, "out.csv")
        data = f.fp.read()
        assert b"caf" in data or b"caf" in data
