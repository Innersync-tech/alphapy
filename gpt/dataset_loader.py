import logging
import os
import re

logger = logging.getLogger(__name__)

BASE_PATH = "data/prompts"


def _sanitize_topic_for_filename(topic: str) -> str:
    """Sanitize a topic string for use as a local prompt filename stem."""
    if not topic:
        return ""
    sanitized = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", topic)
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
    return sanitized.strip()


async def load_topic_context(topic: str) -> str:
    """
    Load topic context from a local .md or .txt file under data/prompts/.

    Returns an empty string when no matching file exists.
    """
    sanitized = _sanitize_topic_for_filename(topic)
    if not sanitized:
        return ""

    stem = sanitized.lower().replace(" ", "_")
    for ext in (".md", ".txt"):
        file_path = os.path.join(BASE_PATH, stem + ext)
        if os.path.exists(file_path):
            logger.debug("Loading context from local file: %s", file_path)
            with open(file_path, encoding="utf-8") as f:
                return f.read().strip()

    return ""
