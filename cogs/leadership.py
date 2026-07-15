import asyncio
import logging

import discord
from discord import app_commands
from discord.app_commands import checks as app_checks
from discord.ext import commands

from gpt.errors import GrokUnavailableError, grok_user_message
from gpt.helpers import ask_gpt, log_gpt_error
from utils.logger import logger
from utils.supabase_client import (
    SupabaseConfigurationError,
    insert_insight_for_discord,
)
from utils.user_messages import ERR_GENERIC

metrics_logger = logging.getLogger(__name__)

class LeaderHelp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="leaderhelp",
        description="Get AI-powered leadership guidance for challenges, team growth, or doubts."
    )
    @app_checks.cooldown(3, 60.0, key=lambda i: (i.guild.id, i.user.id) if i.guild else i.user.id)  # 3 per minute
    async def leaderhelp(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "What kind of support do you want?",
            ephemeral=True,
            view=LeaderOptions(self.bot)
        )

class LeaderOptions(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=90)
        self.bot = bot
        self.add_item(ChallengeSelect(bot))
        self.add_item(AskQuestionButton(bot))

class ChallengeSelect(discord.ui.Select):
    def __init__(self, bot):
        self.bot = bot
        options = [
            discord.SelectOption(label="My team is disengaged", value="disengaged"),
            discord.SelectOption(label="Leadership feels exhausting", value="burnout"),
            discord.SelectOption(label="People are dropping off", value="dropoff"),
            discord.SelectOption(label="I’m doubting myself", value="self_doubt")
        ]
        super().__init__(placeholder="Choose a challenge...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        struggle = self.values[0]
        prompt = f"""
                You're a Discord community leadership coach.
                The leader struggles with: {struggle}.
                Respond with a brief reflection and a practical suggestion.
                Use a supportive, direct tone.
                """
        try:
            logger.info(f"Grok request by {interaction.user} — challenge: {struggle}")
            await interaction.response.defer(ephemeral=True)
            guild_id = interaction.guild.id if interaction.guild else None
            reply = await ask_gpt([{"role": "user", "content": prompt}], user_id=interaction.user.id, guild_id=guild_id)
            # ask_gpt() already logs success internally
            await interaction.followup.send(reply, ephemeral=True)

            async def _store_insight() -> None:
                try:
                    success = await insert_insight_for_discord(
                        interaction.user.id,
                        summary=reply,
                        source="system",
                        tags=["leadership", struggle],
                    )
                    if not success:
                        metrics_logger.debug(
                            "Skipping leadership insight sync: no profile for discord_id=%s",
                            interaction.user.id,
                        )
                except SupabaseConfigurationError:
                    metrics_logger.debug(
                        "Supabase credentials missing; skipping leadership insight sync."
                    )
                except Exception as exc:
                    metrics_logger.warning(
                        "Failed to sync leadership insight for discord_id=%s: %s",
                        interaction.user.id,
                        exc,
                    )

            asyncio.create_task(_store_insight())
        except GrokUnavailableError as e:
            await interaction.followup.send(grok_user_message(e), ephemeral=True)
        except Exception as e:
            logger.exception(f"Unhandled Grok error (ChallengeSelect) by {interaction.user}: {e}")
            # ask_gpt() already logs errors internally
            await interaction.followup.send(ERR_GENERIC, ephemeral=True)

class AskQuestionButton(discord.ui.Button):
    def __init__(self, bot):
        super().__init__(label="Ask your own question", style=discord.ButtonStyle.primary)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("Please type your leadership question (any language):", ephemeral=True)

        def check(m):
            return m.author.id == interaction.user.id and m.channel == interaction.channel

        guild_id = interaction.guild.id if interaction.guild else None
        
        # Step 1: Wait for user message (may fail before ask_gpt is called)
        try:
            msg = await self.bot.wait_for("message", timeout=120.0, check=check)
            user_question = msg.content.strip()
            logger.info(f"{interaction.user} asked: {user_question[:100]}")
        except TimeoutError:
            # Timeout occurred before ask_gpt() - log it
            log_gpt_error(error_type="TimeoutError: User did not respond in time", user_id=interaction.user.id, guild_id=guild_id)
            await interaction.followup.send("❌ You didn't respond in time. Try again later.", ephemeral=True)
            return
        except Exception as e:
            # Other error occurred before ask_gpt() - log it
            logger.exception(f"Error waiting for user message (AskQuestionButton) by {interaction.user}: {e}")
            error_type = f"{type(e).__name__}: {str(e)}"
            log_gpt_error(error_type=error_type, user_id=interaction.user.id, guild_id=guild_id)
            await interaction.followup.send("❌ Error occurred. Try again later.", ephemeral=True)
            return
        
        # Step 2: Call ask_gpt (ask_gpt logs its own errors)
        try:
            from utils.sanitizer import safe_prompt
            prompt = safe_prompt(
                user_question,
                context="You're a supportive leadership coach. A Discord leader asked:"
            )
            prompt += "\nRespond with clarity, honesty, and a helpful suggestion. Keep it short."
        
            await interaction.followup.send("🧠 Thinking...", ephemeral=True)
        
            # ✅ En hier: gewoon rechtstreeks de prompt meesturen
            reply = await ask_gpt(
                [{"role": "user", "content": prompt}],
                user_id=interaction.user.id,
                guild_id=guild_id
            )
            # ask_gpt() already logs success internally
            await interaction.followup.send(reply, ephemeral=True)

            async def _store_question_insight() -> None:
                try:
                    success = await insert_insight_for_discord(
                        interaction.user.id,
                        summary=reply,
                        source="system",
                        tags=["leadership", "ask_question"],
                    )
                    if not success:
                        metrics_logger.debug(
                            "Skipping leadership Q&A insight sync: no profile for discord_id=%s",
                            interaction.user.id,
                        )
                except SupabaseConfigurationError:
                    metrics_logger.debug(
                        "Supabase credentials missing; skipping leadership insight sync."
                    )
                except Exception as exc:
                    metrics_logger.warning(
                        "Failed to sync leadership question insight for discord_id=%s: %s",
                        interaction.user.id,
                        exc,
                    )

            asyncio.create_task(_store_question_insight())

        except GrokUnavailableError as e:
            await interaction.followup.send(grok_user_message(e), ephemeral=True)
        except Exception as e:
            # ask_gpt() already logs all its errors internally, so we don't log again
            logger.exception(f"Unhandled Grok error (AskQuestionButton) by {interaction.user}: {e}")
            await interaction.followup.send("❌ Error occurred. Try again later.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(LeaderHelp(bot))
