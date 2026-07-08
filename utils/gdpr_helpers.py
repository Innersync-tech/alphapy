"""Shared GDPR helpers: view components, acceptance storage, and text builder.

Imported by both cogs/gdpr.py and cogs/configuration.py to avoid circular
imports between cogs.
"""


import asyncpg
import discord
from discord.ext import commands

from utils.db_helpers import acquire_safe, get_bot_db_pool, is_pool_healthy
from utils.logger import logger

# ---------------------------------------------------------------------------
# GDPR agreement text builder
# ---------------------------------------------------------------------------

def build_gdpr_text(guild_name: str) -> str:
    """Return the full GDPR agreement text with the server name substituted."""
    return (
        "**Data Processing and Confidentiality Agreement for Server Members**\n\n"
        "**Effective Date:** 02/11/2025\n\n"
        "**1. Introduction**\n"
        f'As a member of {guild_name} ("the Server"), you may be granted access to certain personal data collected '
        "through our onboarding process and other procedures. This document is intended to make you aware of your responsibilities "
        "regarding the processing, storage, and protection of such data in accordance with the General Data Protection Regulation (GDPR).\n\n"
        "**2. Definitions**\n"
        "- **Personal Data:** Any information relating to an identified or identifiable natural person, including but not limited to name, email address, and other contact or identification details.\n"
        "- **Processing:** Any operation or set of operations performed on personal data, whether or not by automated means, such as collection, recording, organization, storage, consultation, use, disclosure, or deletion.\n\n"
        "**3. Purpose and Scope**\n"
        "This Agreement applies to all members who are granted access to personal data within the Server by virtue of their role or specific access rights. "
        "By obtaining such access, you confirm that you will use this data solely for the purposes for which it was collected and that you will take "
        "all necessary measures to ensure the privacy and security of the data.\n\n"
        "**4. Responsibilities and Obligations**\n"
        "As a member with access to personal data, you agree to:\n"
        "- Treat the personal data confidentially and not disclose it to unauthorized third parties.\n"
        "- Use the data solely for the legitimate purposes defined by the Server.\n"
        "- Implement all necessary technical and organizational measures to prevent unauthorized access, loss, theft, or disclosure of the data.\n"
        "- Immediately report to the Server administrators if you suspect that the data is being processed in violation of the GDPR or if a data breach occurs.\n\n"
        "**5. Security**\n"
        "You commit to protecting the personal data provided using appropriate security measures (such as strong passwords, encryption, and secure storage) "
        "to minimize the risk of unauthorized access or misuse.\n\n"
        "**6. Retention and Deletion**\n"
        "Personal data will be retained only as long as necessary for the legitimate purposes of the Server. If you are responsible for processing data, "
        "you must ensure that any data no longer needed is securely deleted or anonymized.\n\n"
        "**7. Access and Rights**\n"
        "If you have access to personal data through your role or function, you confirm that you will not use this data for purposes inconsistent with the GDPR. "
        "Furthermore, you agree to cooperate with any requests from data subjects for access, correction, or deletion of their personal data.\n\n"
        "**8. Sanctions for Non-Compliance**\n"
        "In the event of a breach of this Agreement or the GDPR, disciplinary measures may be taken, ranging from revoking access to personal data to removal "
        f"from the Server. Additionally, legal action may be pursued if the violation results in damage or data breaches.\n\n"
        "**9. Amendments to the Agreement**\n"
        "This Agreement may be amended periodically. All members will be notified of any changes. It is your responsibility to remain informed of the current terms.\n\n"
        "**10. Acceptance**\n"
        "By gaining access to personal data within the Server, you confirm that you have read, understood, and accept this Agreement. "
        "You acknowledge that you are responsible for protecting the personal data and that you will act in accordance with the GDPR."
    )


# ---------------------------------------------------------------------------
# GDPR acceptance storage
# ---------------------------------------------------------------------------

async def store_gdpr_acceptance(user_id: int, guild_id: int, bot: commands.Bot) -> None:
    """Store GDPR acceptance in PostgreSQL, scoped to a guild."""
    pool = get_bot_db_pool(bot)
    if not is_pool_healthy(pool):
        logger.warning(f"GDPR: Database pool not available for user {user_id}")
        return
    try:
        async with acquire_safe(pool) as conn:
            await conn.execute(
                """
                INSERT INTO gdpr_acceptance (user_id, guild_id, accepted, timestamp)
                VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE
                    SET accepted = $3, timestamp = CURRENT_TIMESTAMP, guild_id = $2;
                """,
                user_id, guild_id, 1,
            )
        logger.info(f"GDPR acceptance saved for user {user_id} in guild {guild_id}")
    except (asyncpg.exceptions.ConnectionDoesNotExistError, asyncpg.exceptions.InterfaceError, ConnectionResetError) as conn_err:
        logger.warning(f"Database connection error saving GDPR acceptance: {conn_err}")
    except Exception as e:
        logger.exception(f"Error saving GDPR acceptance for user {user_id}: {e}")


# ---------------------------------------------------------------------------
# Discord UI components
# ---------------------------------------------------------------------------

class GDPRButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot):
        super().__init__(label="I Agree", style=discord.ButtonStyle.success, custom_id="gdpr_agree")
        self.bot = bot
        self.settings = getattr(bot, "settings", None)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self._is_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "⚠️ GDPR functionality is currently disabled.", ephemeral=True
            )
            return
        await store_gdpr_acceptance(interaction.user.id, interaction.guild_id, self.bot)

        # Assign acceptance role if configured
        role_id = self._get_acceptance_role_id(interaction.guild_id)
        if role_id and interaction.guild:
            role = interaction.guild.get_role(role_id)
            member = interaction.guild.get_member(interaction.user.id)
            if role and member:
                try:
                    await member.add_roles(role, reason="GDPR agreement accepted")
                except discord.Forbidden:
                    logger.warning(f"GDPR: Missing permissions to assign role {role_id} in guild {interaction.guild_id}")
                except Exception as e:
                    logger.error(f"GDPR: Failed to assign acceptance role: {e}")

        await interaction.response.send_message(
            "Thank you for accepting the GDPR terms.", ephemeral=True
        )

    def _is_enabled(self, guild_id: int) -> bool:
        if self.settings:
            try:
                return bool(self.settings.get("gdpr", "enabled", guild_id))
            except KeyError:
                pass
        return True

    def _get_acceptance_role_id(self, guild_id: int) -> int | None:
        if self.settings:
            try:
                value = self.settings.get("gdpr", "acceptance_role_id", guild_id)
                if value:
                    return int(value)
            except (KeyError, TypeError, ValueError):
                pass
        return None


class GDPRView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.add_item(GDPRButton(bot))
