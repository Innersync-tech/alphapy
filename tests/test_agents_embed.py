from agents.base import AgentResult
from cogs.agents import _agent_response_embed


def test_agent_response_embed_uses_display_name_as_title() -> None:
    result = AgentResult(
        agent_name="reflection",
        session_id="a64fdd42-1234-5678-9abc-def012345678",
        summary="Hi there.",
        skill_blocks={"journal_sync": "ctx"},
        display_name="Broski",
    )
    embed = _agent_response_embed(result)
    assert embed.title == "Broski"
    assert embed.footer is not None
    assert "Agent: reflection" in embed.footer.text
    assert "Session a64fdd42" in embed.footer.text
    assert "skills: journal_sync" in embed.footer.text


def test_agent_response_embed_falls_back_to_agent_label_without_display_name() -> None:
    result = AgentResult(
        agent_name="reflection",
        session_id="a64fdd42-1234-5678-9abc-def012345678",
        summary="Hello.",
        skill_blocks={},
    )
    embed = _agent_response_embed(result)
    assert embed.title == "Reflection"
    assert embed.footer is not None
    assert embed.footer.text.startswith("Agent: reflection")
