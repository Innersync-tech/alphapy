"""
Tests for status cog release notes: markdown truncation, product catalog, GitHub fetch helpers.
"""

import pytest

from cogs import status as status_cog
from cogs.status import _drop_dangling_last_header, _truncate_release_notes_md


class TestDropDanglingLastHeader:
    """Tests for _drop_dangling_last_header."""

    def test_returns_unchanged_when_last_line_is_not_header(self):
        text = "Line one.\n\nLine two.\nLast content."
        assert _drop_dangling_last_header(text) == text

    def test_drops_trailing_blank_lines_then_checks_last(self):
        text = "Content here.\n\n## Improved\n"
        assert _drop_dangling_last_header(text) == "Content here."

    def test_drops_single_trailing_header(self):
        text = "Fixed\n- Bullet one\n- Bullet two\n\n## Improved"
        assert _drop_dangling_last_header(text) == "Fixed\n- Bullet one\n- Bullet two"

    def test_drops_header_with_trailing_blanks(self):
        text = "Some text.\n\n## Section\n\n  \n"
        assert _drop_dangling_last_header(text) == "Some text."

    def test_empty_after_strip_returns_empty(self):
        assert _drop_dangling_last_header("\n\n## Only\n\n") == ""

    def test_keeps_content_ending_with_non_header(self):
        text = "Ends with normal line."
        assert _drop_dangling_last_header(text) == text


class TestTruncateReleaseNotesMd:
    """Tests for _truncate_release_notes_md."""

    def test_empty_input_returns_empty(self):
        assert _truncate_release_notes_md("", 100) == ""
        assert _truncate_release_notes_md("   ", 100) == ""

    def test_short_text_unchanged(self):
        text = "Short release notes."
        assert _truncate_release_notes_md(text, 500) == text

    def test_normalizes_leading_hashtag_three(self):
        text = "### Fixed\n- Item"
        assert _truncate_release_notes_md(text, 500).startswith("## Fixed")

    def test_truncates_by_sections_when_over_max(self):
        text = "## Fixed\n- A\n- B\n\n## Improved\n- C\n- D"
        out = _truncate_release_notes_md(text, 25)
        assert len(out) <= 25
        assert "## Fixed" in out or "Fixed" in out
        assert out.strip() and not out.strip().endswith("## Improved")

    def test_result_never_ends_with_bare_header(self):
        text = "## Fixed\n- One\n- Two\n\n## Improved\n- Three"
        for max_len in (20, 40, 60, 100):
            out = _truncate_release_notes_md(text, max_len)
            if out.strip():
                last_line = out.strip().split("\n")[-1].strip()
                assert not last_line.startswith("## "), f"Result should not end with header: {out!r}"

    def test_strips_improved_section_when_ends_with_read_full(self):
        body = "## Fixed\n- Item\n\n## Improved\nRead full release notes on GitHub."
        out = _truncate_release_notes_md(body, 500)
        assert "## Improved" not in out
        assert "Read full release notes on GitHub." not in out
        assert "## Fixed" in out or "Fixed" in out


class TestReleaseProductCatalog:
    def test_alphapy_and_app_present(self):
        assert status_cog.RELEASE_PRODUCT_ALPHAPY in status_cog.RELEASE_PRODUCTS
        assert status_cog.RELEASE_PRODUCT_APP in status_cog.RELEASE_PRODUCTS

    def test_app_is_github_only_and_private(self):
        app = status_cog.RELEASE_PRODUCTS[status_cog.RELEASE_PRODUCT_APP]
        assert app["local_changelog"] is False
        assert app["public_github"] is False
        assert app["use_running_version"] is False
        assert app["repo"] == status_cog.APP_GITHUB_REPO

    def test_alphapy_has_changelog_fallback(self):
        alphapy = status_cog.RELEASE_PRODUCTS[status_cog.RELEASE_PRODUCT_ALPHAPY]
        assert alphapy["local_changelog"] is True
        assert alphapy["public_github"] is True
        assert alphapy["use_running_version"] is True

    def test_unknown_product_resolves_to_alphapy(self):
        assert status_cog._resolve_release_product(None) == status_cog.RELEASE_PRODUCT_ALPHAPY
        assert status_cog._resolve_release_product("nope") == status_cog.RELEASE_PRODUCT_ALPHAPY


class TestGithubReleaseUrls:
    def test_latest_when_tag_none(self):
        url = status_cog._github_release_api_url("org/repo", None)
        assert url == "https://api.github.com/repos/org/repo/releases/latest"

    def test_tag_url(self):
        url = status_cog._github_release_api_url("org/repo", "v3.8.0")
        assert url == "https://api.github.com/repos/org/repo/releases/tags/v3.8.0"

    def test_normalize_tag(self):
        assert status_cog._normalize_release_tag("3.8.0") == "v3.8.0"
        assert status_cog._normalize_release_tag("v3.8.0") == "v3.8.0"
        assert status_cog._normalize_release_tag(None) is None
        assert status_cog._normalize_release_tag("  ") is None


class TestGithubRequestHeaders:
    def test_no_auth_when_token_unset(self, monkeypatch):
        monkeypatch.setattr(status_cog.config, "GITHUB_TOKEN", "")
        headers = status_cog._github_request_headers()
        assert "Authorization" not in headers
        assert headers["Accept"] == "application/vnd.github+json"

    def test_bearer_when_token_set(self, monkeypatch):
        monkeypatch.setattr(status_cog.config, "GITHUB_TOKEN", "ghp_test")
        headers = status_cog._github_request_headers()
        assert headers["Authorization"] == "Bearer ghp_test"


class TestProductGithubRepo:
    def test_app_hardcoded_ignores_github_repo_env(self, monkeypatch):
        monkeypatch.setattr(status_cog.config, "GITHUB_REPO", "Innersync-tech/alphapy")
        assert status_cog._product_github_repo("app") == "Innersync-tech/innersync-dashboard"

    def test_alphapy_uses_config(self, monkeypatch):
        monkeypatch.setattr(status_cog.config, "GITHUB_REPO", "other/alphapy")
        assert status_cog._product_github_repo("alphapy") == "other/alphapy"

    def test_alphapy_defaults_when_unset(self, monkeypatch):
        monkeypatch.setattr(status_cog.config, "GITHUB_REPO", "")
        assert status_cog._product_github_repo("alphapy") == "Innersync-tech/alphapy"


@pytest.mark.asyncio
async def test_app_without_token_does_not_fetch(monkeypatch):
    monkeypatch.setattr(status_cog.config, "GITHUB_TOKEN", "")

    async def _should_not_fetch(*_a, **_k):
        raise AssertionError("must not call GitHub without token")

    monkeypatch.setattr(status_cog, "_fetch_github_release", _should_not_fetch)
    result = await status_cog._get_release_notes("app", None)
    assert result.error == status_cog.APP_RELEASE_TOKEN_MISSING_MSG
    assert result.notes == ""
    assert result.github_url is None


@pytest.mark.asyncio
async def test_app_does_not_fall_back_to_changelog(monkeypatch):
    monkeypatch.setattr(status_cog.config, "GITHUB_TOKEN", "tok")

    async def _empty(*_a, **_k):
        return None, None, None, 404

    async def _local_should_not_run(*_a, **_k):
        raise AssertionError("App must not read local changelog")

    monkeypatch.setattr(status_cog, "_fetch_github_release", _empty)
    monkeypatch.setattr(status_cog, "_read_release_notes", _local_should_not_run)
    result = await status_cog._get_release_notes("app", "3.8.0")
    assert result.notes == ""
    assert result.error is None
    assert result.github_url is None
    assert result.version == "3.8.0"


@pytest.mark.asyncio
async def test_app_success_omits_github_url(monkeypatch):
    monkeypatch.setattr(status_cog.config, "GITHUB_TOKEN", "tok")

    async def _ok(*_a, **_k):
        return (
            "## Fixed\n- A",
            "https://github.com/Innersync-tech/innersync-dashboard/releases/tag/v3.8.0",
            "v3.8.0",
            200,
        )

    monkeypatch.setattr(status_cog, "_fetch_github_release", _ok)
    result = await status_cog._get_release_notes("app", None)
    assert result.notes.startswith("## Fixed")
    assert result.github_url is None
    assert result.version == "3.8.0"
    assert result.public_url
    assert result.footer == "Innersync App"
    assert result.label == "App"


@pytest.mark.asyncio
async def test_alphapy_falls_back_to_changelog(monkeypatch):
    async def _fail(*_a, **_k):
        return None, None, None, 404

    async def _local(_path, version):
        assert version == "9.9.9"
        return "local notes"

    monkeypatch.setattr(status_cog, "_fetch_github_release", _fail)
    monkeypatch.setattr(status_cog, "_read_release_notes", _local)
    result = await status_cog._get_release_notes("alphapy", "9.9.9")
    assert result.notes == "local notes"
    assert result.github_url and result.github_url.endswith("/v9.9.9")
    assert result.label == "Alphapy"


@pytest.mark.asyncio
async def test_alphapy_uses_github_url_when_public(monkeypatch):
    async def _ok(*_a, **_k):
        return (
            "gh notes",
            "https://github.com/Innersync-tech/alphapy/releases/tag/v3.14.0",
            "v3.14.0",
            200,
        )

    monkeypatch.setattr(status_cog, "_fetch_github_release", _ok)
    result = await status_cog._get_release_notes("alphapy", "3.14.0")
    assert result.notes == "gh notes"
    assert result.github_url == "https://github.com/Innersync-tech/alphapy/releases/tag/v3.14.0"


@pytest.mark.asyncio
async def test_app_401_returns_invalid_token_message(monkeypatch):
    monkeypatch.setattr(status_cog.config, "GITHUB_TOKEN", "bad")

    async def _unauth(*_a, **_k):
        return None, None, None, 401

    monkeypatch.setattr(status_cog, "_fetch_github_release", _unauth)
    result = await status_cog._get_release_notes("app", "3.8.0")
    assert result.error == status_cog.APP_RELEASE_TOKEN_INVALID_MSG
    assert result.notes == ""


@pytest.mark.asyncio
async def test_app_403_returns_scope_message(monkeypatch):
    monkeypatch.setattr(status_cog.config, "GITHUB_TOKEN", "limited")

    async def _forbidden(*_a, **_k):
        return None, None, None, 403

    monkeypatch.setattr(status_cog, "_fetch_github_release", _forbidden)
    result = await status_cog._get_release_notes("app", "3.8.0")
    assert result.error == status_cog.APP_RELEASE_TOKEN_SCOPE_MSG
    assert result.notes == ""
