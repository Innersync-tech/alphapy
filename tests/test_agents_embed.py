from unittest.mock import MagicMock

from agents.base import AgentResult
from cogs.agents import (
    _agent_app_link_view,
    _agent_emoji,
    _agent_response_embed,
    _app_agent_home_url,
)


def test_agent_emoji_for_known_and_default() -> None:
    assert _agent_emoji("reflection") == "🪞"
    assert _agent_emoji("unknown_agent") == "✨"


def test_agent_response_embed_uses_display_name_as_title() -> None:
    result = AgentResult(
        agent_name="reflection",
        session_id="a64fdd42-1234-5678-9abc-def012345678",
        summary="Hi there.",
        skill_blocks={"journal_sync": "ctx"},
        display_name="Broski",
    )
    embed = _agent_response_embed(result)
    assert embed.title == "🪞 Broski"
    assert embed.footer is not None
    assert "Agent: reflection" in embed.footer.text
    assert "Session a64fdd42" in embed.footer.text
    assert "skills: journal_sync" in embed.footer.text
    assert "innersync.tech" not in embed.footer.text


def test_agent_response_embed_falls_back_to_agent_label_without_display_name() -> None:
    result = AgentResult(
        agent_name="reflection",
        session_id="a64fdd42-1234-5678-9abc-def012345678",
        summary="Hello.",
        skill_blocks={},
    )
    embed = _agent_response_embed(result)
    assert embed.title == "🪞 Reflection"
    assert embed.footer is not None
    assert embed.footer.text.startswith("Agent: reflection")


def test_agent_response_embed_sets_bot_author_when_provided() -> None:
    result = AgentResult(
        agent_name="reflection",
        session_id="a64fdd42-1234-5678-9abc-def012345678",
        summary="Hello.",
        skill_blocks={},
    )
    bot_user = MagicMock()
    bot_user.display_name = "Alphapy"
    bot_user.display_avatar.url = "https://cdn.discordapp.com/avatars/1/abc.png"
    embed = _agent_response_embed(result, bot_user=bot_user)
    assert embed.author is not None
    assert embed.author.name == "Alphapy"
    assert embed.author.icon_url == "https://cdn.discordapp.com/avatars/1/abc.png"


def test_agent_response_embed_tolerates_mock_bot_user_without_str_name() -> None:
    """Interaction mocks often leave display_name as MagicMock — must not crash."""
    result = AgentResult(
        agent_name="reflection",
        session_id="a64fdd42-1234-5678-9abc-def012345678",
        summary="Hello.",
        skill_blocks={},
    )
    bot_user = MagicMock()  # display_name / avatar.url are MagicMocks
    embed = _agent_response_embed(result, bot_user=bot_user)
    assert embed.author is not None
    assert embed.author.name == "Alphapy"
    assert embed.author.icon_url is None


def test_agent_app_link_view_points_to_dashboard_agent() -> None:
    view = _agent_app_link_view()
    assert len(view.children) == 1
    button = view.children[0]
    assert button.label == "Continue in App"
    assert button.url == _app_agent_home_url()
