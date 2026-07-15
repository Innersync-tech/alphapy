import asyncio
import logging

import discord
from discord import app_commands
from discord.app_commands import checks as app_checks
from discord.ext import commands

from gpt.errors import GrokUnavailableError, grok_user_message
from gpt.helpers import ask_gpt
from utils.supabase_client import (
    SupabaseConfigurationError,
    insert_insight_for_discord,
)
from utils.user_messages import ERR_GENERIC

logger = logging.getLogger(__name__)

STYLES = [
    "punchy",
    "vulnerable",
    "educational",
    "spiritual",
    "motivational",
    "formal"
]

class ContentGen(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="create_caption", description="Generate a social caption based on topic and style")
    @app_checks.cooldown(3, 60.0, key=lambda i: (i.guild.id, i.user.id) if i.guild else i.user.id)  # 3 per minute
    @app_commands.describe(
        topic="What should the caption be about? (e.g. trading discipline, mindset, risk)",
        style="Choose the tone of the caption"
    )
    @app_commands.choices(style=[
        app_commands.Choice(name=style.capitalize(), value=style) for style in STYLES
    ])
    async def create_caption(self, interaction: discord.Interaction, topic: str, style: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True, thinking=True)

        from utils.sanitizer import safe_prompt
        
        safe_topic = safe_prompt(topic, context="Generate a short caption (max 300 characters) for social media.")
        user_content = f"""
Tone: {style.value}
Topic: {safe_topic}
Be original, real, and in tune with an audience that's self-aware and growth-oriented.
Avoid clichés.
"""
        
        messages = [
            {"role": "system", "content": "You are an AI content creator."},
            {"role": "user", "content": user_content}
        ]

        guild_id = interaction.guild.id if interaction.guild else None
        
        # Call ask_gpt (ask_gpt logs its own errors)
        try:
            reply = await ask_gpt(messages, user_id=interaction.user.id, guild_id=guild_id)
            # ask_gpt() already logs success internally
            await interaction.followup.send(reply.strip(), ephemeral=True)

            async def _store_caption_insight() -> None:
                try:
                    success = await insert_insight_for_discord(
                        interaction.user.id,
                        summary=reply.strip(),
                        source="system",
                        tags=["content", style.value],
                    )
                    if not success:
                        logger.debug(
                            "Skipping content insight sync: no profile for discord_id=%s",
                            interaction.user.id,
                        )
                except SupabaseConfigurationError:
                    logger.debug(
                        "Supabase credentials missing; skipping content insight sync."
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to sync content insight for discord_id=%s: %s",
                        interaction.user.id,
                        exc,
                    )

            asyncio.create_task(_store_caption_insight())
        except GrokUnavailableError as e:
            await interaction.followup.send(grok_user_message(e), ephemeral=True)
        except Exception:
            # ask_gpt() already logs all its errors internally, so we don't log again
            await interaction.followup.send(ERR_GENERIC, ephemeral=True)

async def setup(bot):
    await bot.add_cog(ContentGen(bot))
