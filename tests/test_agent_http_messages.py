from agents.http_routes import _format_messages
from agents.policy import build_agent_user_message


def test_format_messages_strips_internal_user_prompt() -> None:
    bloated = build_agent_user_message(
        context_blob="[journal_sync]\nSecret reflection",
        user_request="hey",
    )
    rows = [
        {"turn_index": 0, "role": "user", "content": bloated},
        {"turn_index": 0, "role": "assistant", "content": "Hello there."},
    ]
    messages = _format_messages(rows)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "hey"
    assert messages[1].content == "Hello there."
