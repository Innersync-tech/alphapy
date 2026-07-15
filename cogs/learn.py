import asyncio
import logging

import discord
from discord import app_commands
from discord.app_commands import checks as app_checks
from discord.ext import commands

from gpt.dataset_loader import load_topic_context
from gpt.errors import GrokUnavailableError, grok_user_message
from gpt.helpers import ask_gpt, is_allowed_prompt, log_gpt_error
from utils.supabase_client import (
    SupabaseConfigurationError,
    insert_insight_for_discord,
)
from utils.user_messages import ERR_GENERIC

logger = logging.getLogger(__name__)

class LearnTopic(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="learn_topic", description="Ask a topic and get a short, clear explanation from Grok.")
    @app_checks.cooldown(3, 60.0, key=lambda i: (i.guild.id, i.user.id) if i.guild else i.user.id)  # 3 per minute
    @app_commands.describe(topic="e.g. RSI, scalping, risk management…")
    async def learn_topic(self, interaction: discord.Interaction, topic: str):
        guild_id = interaction.guild.id if interaction.guild else None
        
        # Defer the interaction response
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception as e:
            # Error during defer - log it and use response.send_message() instead
            error_type = f"{type(e).__name__}: {str(e)}"
            log_gpt_error(error_type=error_type, user_id=interaction.user.id, guild_id=guild_id)
            try:
                await interaction.response.send_message(
                    ERR_GENERIC,
                    ephemeral=True,
                )
            except Exception:
                # If response is already used, try followup as fallback
                await interaction.followup.send(
                    ERR_GENERIC,
                    ephemeral=True,
                )
            return

        if not is_allowed_prompt(topic):
            await interaction.followup.send(
                "❌ That question doesn't align with Innersync • Alphapy's intent. Try a more purposeful topic.",
                ephemeral=True
            )
            log_gpt_error("filtered_prompt", user_id=interaction.user.id, guild_id=guild_id)
            return
        
        # Step 1: Load topic context (may fail before ask_gpt is called)
        try:
            context = await load_topic_context(topic)
        except Exception as e:
            # Error occurred before ask_gpt() - log it
            error_type = f"{type(e).__name__}: {str(e)}"
            log_gpt_error(error_type=error_type, user_id=interaction.user.id, guild_id=guild_id)
            await interaction.followup.send("❌ Couldn't generate a response. Try again later.", ephemeral=True)
            return

        # Step 2: Prepare prompt and call ask_gpt (ask_gpt logs its own errors)
        try:
            from gpt.helpers import LEARN_TOPIC_PROMPT_TEMPLATE
            from utils.sanitizer import safe_prompt
            
            # Build prompt: use context as background info, topic as the question
            if context:
                # Context found: use it as background information, topic as the question
                sanitized_context = safe_prompt(context)
                sanitized_topic = safe_prompt(topic)
                # Note: .format() only interprets braces in the template, not in replacement values.
                # Values are inserted verbatim, so curly braces in context/topic are safe.
                prompt_content = LEARN_TOPIC_PROMPT_TEMPLATE.format(
                    context=sanitized_context,
                    topic=sanitized_topic
                )
            else:
                # No context: treat topic as a direct question
                sanitized_topic = safe_prompt(topic)
                prompt_content = sanitized_topic
            
            prompt_messages = [{"role": "user", "content": prompt_content}]

            # Keep-alive: edit deferred message every 10s while Grok runs to avoid Discord interaction timeout
            keepalive_interval = 10.0
            keepalive_task: asyncio.Task | None = None

            async def _keepalive_loop() -> None:
                try:
                    while True:
                        await asyncio.sleep(keepalive_interval)
                        try:
                            await interaction.edit_original_response(
                                content="Still working on it…"
                            )
                        except Exception:
                            return
                except asyncio.CancelledError:
                    pass

            keepalive_task = asyncio.create_task(_keepalive_loop())
            try:
                reply = await ask_gpt(
                    prompt_messages,
                    user_id=interaction.user.id,
                    guild_id=guild_id,
                )
            finally:
                keepalive_task.cancel()
                try:
                    await keepalive_task
                except asyncio.CancelledError:
                    pass

            # ask_gpt() already logs success; replace thinking/keepalive with final reply
            try:
                await interaction.edit_original_response(content=reply)
            except Exception:
                await interaction.followup.send(reply, ephemeral=True)

            async def _store_learn_insight() -> None:
                try:
                    success = await insert_insight_for_discord(
                        interaction.user.id,
                        summary=reply,
                        source="system",
                        tags=["learn_topic", topic.lower()],
                    )
                    if not success:
                        logger.debug(
                            "Skipping learn insight sync: no profile for discord_id=%s",
                            interaction.user.id,
                        )
                except SupabaseConfigurationError:
                    logger.debug(
                        "Supabase credentials missing; skipping learn insight sync."
                    )
                except Exception as exc:  # pragma: no cover - network path
                    logger.warning(
                        "Failed to sync learn insight for discord_id=%s: %s",
                        interaction.user.id,
                        exc,
                    )

            asyncio.create_task(_store_learn_insight())

        except GrokUnavailableError as e:
            await interaction.followup.send(grok_user_message(e), ephemeral=True)
        except Exception:
            # ask_gpt() already logs all its errors internally, so we don't log again
            await interaction.followup.send("❌ Couldn't generate a response. Try again later.", ephemeral=True)




async def setup(bot):
    await bot.add_cog(LearnTopic(bot))

