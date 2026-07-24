"""
Custom Commands System for Alphapy

Guild admins can define automated message responses triggered by specific
message patterns. Supports four trigger types (exact, starts_with, contains,
regex) and dynamic variable substitution in responses.
"""

import logging
import random
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils.db_helpers import acquire_safe, get_bot_db_pool
from utils.embed_builder import EmbedBuilder
from utils.sanitizer import safe_embed_text
from utils.settings_helpers import is_module_enabled
from utils.validators import validate_admin

logger = logging.getLogger(__name__)

# Cache TTL in seconds
CACHE_TTL = 60.0

# Per-guild command limit
MAX_COMMANDS_PER_GUILD = 50

# Field length limits
MAX_TRIGGER_LEN = 200
MAX_RESPONSE_LEN = 1900
MAX_NAME_LEN = 50

TRIGGER_TYPES = ["exact", "starts_with", "contains", "regex"]

VARIABLE_HELP = (
    "`{user}` — mention the member\n"
    "`{user.name}` — display name\n"
    "`{server}` — server name\n"
    "`{channel}` — channel mention\n"
    "`{uses}` — trigger use count\n"
    "`{random:a|b|c}` — random pick"
)


def requires_admin():
    """Check decorator for admin permissions."""
    async def predicate(interaction: discord.Interaction) -> bool:
        is_admin, _ = await validate_admin(interaction, raise_on_fail=False)
        if is_admin:
            return True
        raise app_commands.CheckFailure("You need administrator permissions for this command.")
    return app_commands.check(predicate)


def _resolve_response(template: str, message: discord.Message, uses: int) -> str:
    """Resolve dynamic variables in a response template."""
    result = template

    # {random:a|b|c}
    def pick_random(m: re.Match) -> str:
        options = [o.strip() for o in m.group(1).split("|") if o.strip()]
        return random.choice(options) if options else ""

    result = re.sub(r"\{random:([^}]+)\}", pick_random, result)

    # Longer placeholders must run before `{user}` so `{user.name}` is not corrupted.
    result = result.replace("{user.name}", message.author.display_name)
    result = result.replace("{user}", message.author.mention)
    result = result.replace("{server}", message.guild.name if message.guild else "")
    result = result.replace("{channel}", message.channel.mention)
    result = result.replace("{uses}", str(uses))

    return result


# Stay under Discord's 4096-char embed description limit (margin for safety).
_CC_LIST_PAGE_DESC_MAX = 3900


def _paginate_list_lines(lines: list[str], max_chars: int = _CC_LIST_PAGE_DESC_MAX) -> list[str]:
    """Split line strings into page bodies; each joined page is at most max_chars."""
    if not lines:
        return []

    pages: list[str] = []
    chunk: list[str] = []
    size = 0
    for line in lines:
        add = len(line) + (1 if chunk else 0)
        if chunk and size + add > max_chars:
            pages.append("\n".join(chunk))
            chunk = [line]
            size = len(line)
        else:
            chunk.append(line)
            size += add
    if chunk:
        pages.append("\n".join(chunk))
    return pages


def _trigger_matches(trigger_type: str, trigger_value: str, content: str, case_sensitive: bool) -> bool:
    """Return True if the message content matches the trigger."""
    compare = content if case_sensitive else content.lower()
    pattern = trigger_value if case_sensitive else trigger_value.lower()

    if trigger_type == "exact":
        return compare == pattern
    elif trigger_type == "starts_with":
        return compare.startswith(pattern)
    elif trigger_type == "contains":
        return pattern in compare
    elif trigger_type == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            return bool(re.search(trigger_value, content, flags))
        except re.error:
            return False
    return False


class EditCommandModal(discord.ui.Modal, title="Edit Custom Command"):
    """Modal for editing an existing custom command."""

    trigger_value = discord.ui.TextInput(
        label="Trigger",
        max_length=MAX_TRIGGER_LEN,
        required=True,
    )
    response = discord.ui.TextInput(
        label="Response",
        style=discord.TextStyle.paragraph,
        max_length=MAX_RESPONSE_LEN,
        required=True,
    )

    def __init__(self, cog: "CustomCommandsCog", row: dict):
        super().__init__()
        self.cog = cog
        self.row = row
        self.trigger_value.default = row["trigger_value"]
        self.response.default = row["response"]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pool = get_bot_db_pool(self.cog.bot)
        if not pool:
            await interaction.response.send_message(
                embed=EmbedBuilder.error("Database Unavailable", "Cannot connect to the database."),
                ephemeral=True,
            )
            return

        new_trigger = self.trigger_value.value.strip()
        new_response = self.response.value.strip()

        # Validate regex if needed
        if self.row["trigger_type"] == "regex":
            try:
                re.compile(new_trigger)
            except re.error as e:
                await interaction.response.send_message(
                    embed=EmbedBuilder.error("Invalid Regex", f"Pattern error: `{e}`"),
                    ephemeral=True,
                )
                return

        async with acquire_safe(pool) as conn:
            await conn.execute(
                """
                UPDATE custom_commands
                SET trigger_value = $1, response = $2, updated_at = NOW()
                WHERE id = $3
                """,
                new_trigger, new_response, self.row["id"],
            )

        if interaction.guild:
            self.cog._invalidate_cache(interaction.guild.id)

        await interaction.response.send_message(
            embed=EmbedBuilder.success(
                "Command Updated",
                f"**{safe_embed_text(self.row['name'])}** has been updated.",
            ),
            ephemeral=True,
        )


class DeleteConfirmView(discord.ui.View):
    """Confirmation view for deleting a custom command."""

    def __init__(self, cog: "CustomCommandsCog", row: dict):
        super().__init__(timeout=30)
        self.cog = cog
        self.row = row

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        pool = get_bot_db_pool(self.cog.bot)
        if not pool:
            await interaction.response.send_message(
                embed=EmbedBuilder.error("Database Unavailable", "Cannot connect to the database."),
                ephemeral=True,
            )
            self.stop()
            return

        async with acquire_safe(pool) as conn:
            await conn.execute("DELETE FROM custom_commands WHERE id = $1", self.row["id"])

        if interaction.guild:
            self.cog._invalidate_cache(interaction.guild.id)

        await interaction.response.edit_message(
            embed=EmbedBuilder.success(
                "Command Deleted",
                f"**{safe_embed_text(self.row['name'])}** has been deleted.",
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=EmbedBuilder.info("Cancelled", "Deletion cancelled."),
            view=None,
        )
        self.stop()


class CustomCommandsCog(commands.Cog):
    """Custom commands system — guild-defined message triggers and responses."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id -> (timestamp, list[dict])
        self._cache: dict[int, tuple[float, list[dict]]] = {}

    async def cog_load(self):
        logger.info("Loading CustomCommands cog...")

    async def cog_unload(self):
        self._cache.clear()
        logger.info("CustomCommands cog unloaded.")

    # ------------------------------------------------------------------ cache

    def _invalidate_cache(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)

    async def _get_commands(self, guild_id: int) -> list[dict]:
        """Return guild's enabled custom commands, using cache when fresh."""
        cached = self._cache.get(guild_id)
        if cached and (time.monotonic() - cached[0]) < CACHE_TTL:
            return cached[1]

        pool = get_bot_db_pool(self.bot)
        if not pool:
            return []

        async with acquire_safe(pool) as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, trigger_type, trigger_value, response,
                       case_sensitive, delete_trigger, reply_to_user, uses
                FROM custom_commands
                WHERE guild_id = $1 AND enabled = true
                ORDER BY id
                """,
                guild_id,
            )

        result = [dict(r) for r in rows]
        self._cache[guild_id] = (time.monotonic(), result)
        return result

    # ---------------------------------------------------------- message listener

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not message.guild:
            return

        if not is_module_enabled(self.bot, message.guild.id, "custom_commands"):
            return

        content = message.content
        if not content:
            return

        commands_list = await self._get_commands(message.guild.id)
        if not commands_list:
            return

        for cmd in commands_list:
            if not _trigger_matches(
                cmd["trigger_type"],
                cmd["trigger_value"],
                content,
                cmd["case_sensitive"],
            ):
                continue

            # Resolve response
            resolved = _resolve_response(cmd["response"], message, cmd["uses"])

            # Send response
            try:
                if cmd["reply_to_user"]:
                    await message.reply(resolved, mention_author=False)
                else:
                    await message.channel.send(resolved)
            except discord.HTTPException as e:
                logger.warning(f"CustomCommands: failed to send response for '{cmd['name']}': {e}")
                break

            # Delete trigger message if configured
            if cmd["delete_trigger"]:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass

            # Increment use counter (fire-and-forget, don't block)
            self.bot.loop.create_task(self._increment_uses(cmd["id"], message.guild.id))

            break  # Only fire first matching command

    async def _increment_uses(self, command_id: int, guild_id: int) -> None:
        pool = get_bot_db_pool(self.bot)
        if not pool:
            return
        try:
            async with acquire_safe(pool) as conn:
                await conn.execute(
                    "UPDATE custom_commands SET uses = uses + 1 WHERE id = $1",
                    command_id,
                )
            # Invalidate cache so the next fetch reflects updated use count
            self._invalidate_cache(guild_id)
        except Exception as e:
            logger.debug(f"CustomCommands: failed to increment uses for id={command_id}: {e}")

    # ---------------------------------------------------------- slash commands

    cc = app_commands.Group(
        name="cc",
        description="Manage custom commands for this server.",
    )

    @cc.command(name="add", description="Add a new custom command.")
    @app_commands.describe(
        name="Unique name/slug for this command (e.g. 'hello')",
        trigger_type="How the trigger is matched against messages",
        trigger="The text or regex pattern to match",
        response="Response to send (supports {user}, {server}, {random:a|b} etc.)",
        case_sensitive="Match case-sensitively (default: false)",
        delete_trigger="Delete the triggering message (default: false)",
        reply_to_user="Reply to the user's message (default: true)",
    )
    @app_commands.choices(trigger_type=[
        app_commands.Choice(name="Exact match", value="exact"),
        app_commands.Choice(name="Starts with", value="starts_with"),
        app_commands.Choice(name="Contains", value="contains"),
        app_commands.Choice(name="Regex", value="regex"),
    ])
    @requires_admin()
    async def cc_add(
        self,
        interaction: discord.Interaction,
        name: str,
        trigger_type: app_commands.Choice[str],
        trigger: str,
        response: str,
        case_sensitive: bool = False,
        delete_trigger: bool = False,
        reply_to_user: bool = True,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        name = name.strip().lower()
        trigger = trigger.strip()
        response = response.strip()

        # Validate name
        if not name or len(name) > MAX_NAME_LEN:
            await interaction.followup.send(
                embed=EmbedBuilder.error("Invalid Name", f"Name must be 1–{MAX_NAME_LEN} characters."),
                ephemeral=True,
            )
            return

        # Validate lengths
        if len(trigger) > MAX_TRIGGER_LEN:
            await interaction.followup.send(
                embed=EmbedBuilder.error("Trigger Too Long", f"Trigger must be at most {MAX_TRIGGER_LEN} characters."),
                ephemeral=True,
            )
            return

        if len(response) > MAX_RESPONSE_LEN:
            await interaction.followup.send(
                embed=EmbedBuilder.error("Response Too Long", f"Response must be at most {MAX_RESPONSE_LEN} characters."),
                ephemeral=True,
            )
            return

        # Validate regex pattern
        if trigger_type.value == "regex":
            try:
                re.compile(trigger)
            except re.error as e:
                await interaction.followup.send(
                    embed=EmbedBuilder.error("Invalid Regex", f"Pattern error: `{e}`"),
                    ephemeral=True,
                )
                return

        pool = get_bot_db_pool(self.bot)
        if not pool:
            await interaction.followup.send(
                embed=EmbedBuilder.error("Database Unavailable", "Cannot connect to the database."),
                ephemeral=True,
            )
            return

        guild_id = interaction.guild.id

        async with acquire_safe(pool) as conn:
            # Check limit
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM custom_commands WHERE guild_id = $1",
                guild_id,
            )
            if count >= MAX_COMMANDS_PER_GUILD:
                await interaction.followup.send(
                    embed=EmbedBuilder.warning(
                        "Limit Reached",
                        f"This server already has {MAX_COMMANDS_PER_GUILD} custom commands. "
                        "Delete one before adding more.",
                    ),
                    ephemeral=True,
                )
                return

            # Check name uniqueness
            existing = await conn.fetchval(
                "SELECT id FROM custom_commands WHERE guild_id = $1 AND name = $2",
                guild_id, name,
            )
            if existing:
                await interaction.followup.send(
                    embed=EmbedBuilder.error(
                        "Name Already Exists",
                        f"A command named **{safe_embed_text(name)}** already exists. "
                        "Use `/cc edit` to modify it.",
                    ),
                    ephemeral=True,
                )
                return

            await conn.execute(
                """
                INSERT INTO custom_commands
                    (guild_id, name, trigger_type, trigger_value, response,
                     case_sensitive, delete_trigger, reply_to_user, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                guild_id, name, trigger_type.value, trigger, response,
                case_sensitive, delete_trigger, reply_to_user,
                interaction.user.id,
            )

        self._invalidate_cache(guild_id)

        embed = EmbedBuilder.success(
            "Custom Command Added",
            f"**{safe_embed_text(name)}** is now active.",
            fields=[
                {"name": "Trigger type", "value": trigger_type.name, "inline": True},
                {"name": "Trigger", "value": f"`{safe_embed_text(trigger)}`", "inline": True},
                {"name": "Case sensitive", "value": "Yes" if case_sensitive else "No", "inline": True},
                {"name": "Response", "value": safe_embed_text(response, max_length=512), "inline": False},
                {"name": "Available variables", "value": VARIABLE_HELP, "inline": False},
            ],
            footer="Use /cc list to see all commands",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @cc.command(name="edit", description="Edit an existing custom command via a modal.")
    @app_commands.describe(name="Name of the command to edit")
    @requires_admin()
    async def cc_edit(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        pool = get_bot_db_pool(self.bot)
        if not pool:
            await interaction.response.send_message(
                embed=EmbedBuilder.error("Database Unavailable", "Cannot connect to the database."),
                ephemeral=True,
            )
            return

        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM custom_commands WHERE guild_id = $1 AND name = $2",
                interaction.guild.id, name.strip().lower(),
            )

        if not row:
            await interaction.response.send_message(
                embed=EmbedBuilder.error("Not Found", f"No command named **{safe_embed_text(name)}** found."),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(EditCommandModal(self, dict(row)))

    @cc.command(name="delete", description="Delete a custom command.")
    @app_commands.describe(name="Name of the command to delete")
    @requires_admin()
    async def cc_delete(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        pool = get_bot_db_pool(self.bot)
        if not pool:
            await interaction.response.send_message(
                embed=EmbedBuilder.error("Database Unavailable", "Cannot connect to the database."),
                ephemeral=True,
            )
            return

        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM custom_commands WHERE guild_id = $1 AND name = $2",
                interaction.guild.id, name.strip().lower(),
            )

        if not row:
            await interaction.response.send_message(
                embed=EmbedBuilder.error("Not Found", f"No command named **{safe_embed_text(name)}** found."),
                ephemeral=True,
            )
            return

        embed = EmbedBuilder.warning(
            "Confirm Deletion",
            f"Are you sure you want to delete **{safe_embed_text(row['name'])}**? This cannot be undone.",
        )
        await interaction.response.send_message(embed=embed, view=DeleteConfirmView(self, dict(row)), ephemeral=True)

    @cc.command(name="list", description="List all custom commands for this server.")
    @requires_admin()
    async def cc_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        pool = get_bot_db_pool(self.bot)
        if not pool:
            await interaction.followup.send(
                embed=EmbedBuilder.error("Database Unavailable", "Cannot connect to the database."),
                ephemeral=True,
            )
            return

        async with acquire_safe(pool) as conn:
            rows = await conn.fetch(
                """
                SELECT name, trigger_type, trigger_value, enabled, uses
                FROM custom_commands
                WHERE guild_id = $1
                ORDER BY name
                """,
                interaction.guild.id,
            )

        if not rows:
            await interaction.followup.send(
                embed=EmbedBuilder.info(
                    "No Custom Commands",
                    "This server has no custom commands yet.\nUse `/cc add` to create one.",
                ),
                ephemeral=True,
            )
            return

        type_icons = {
            "exact": "🎯",
            "starts_with": "▶️",
            "contains": "🔍",
            "regex": "🔣",
        }

        lines = []
        for row in rows:
            icon = type_icons.get(row["trigger_type"], "❓")
            status = "✅" if row["enabled"] else "❌"
            trigger_preview = safe_embed_text(row["trigger_value"], max_length=40)
            lines.append(
                f"{status} **{safe_embed_text(row['name'])}** — {icon} `{trigger_preview}` ({row['uses']} uses)"
            )

        pages = _paginate_list_lines(lines)
        total_pages = len(pages)
        base_title = f"Custom Commands ({len(rows)}/{MAX_COMMANDS_PER_GUILD})"
        for i, description in enumerate(pages):
            title = f"{base_title} · Page {i + 1}/{total_pages}" if total_pages > 1 else base_title
            footer = (
                f"Page {i + 1}/{total_pages} · Use /cc view <name> for details"
                if total_pages > 1
                else "Use /cc view <name> for details"
            )
            embed = EmbedBuilder.info(title, description, footer=footer)
            if i == 0:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)

    @cc.command(name="view", description="View details of a specific custom command.")
    @app_commands.describe(name="Name of the command to view")
    @requires_admin()
    async def cc_view(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        pool = get_bot_db_pool(self.bot)
        if not pool:
            await interaction.followup.send(
                embed=EmbedBuilder.error("Database Unavailable", "Cannot connect to the database."),
                ephemeral=True,
            )
            return

        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM custom_commands WHERE guild_id = $1 AND name = $2",
                interaction.guild.id, name.strip().lower(),
            )

        if not row:
            await interaction.followup.send(
                embed=EmbedBuilder.error("Not Found", f"No command named **{safe_embed_text(name)}** found."),
                ephemeral=True,
            )
            return

        type_labels = {
            "exact": "🎯 Exact match",
            "starts_with": "▶️ Starts with",
            "contains": "🔍 Contains",
            "regex": "🔣 Regex",
        }

        embed = EmbedBuilder.info(
            f"Command: {safe_embed_text(row['name'])}",
            fields=[
                {"name": "Status", "value": "✅ Enabled" if row["enabled"] else "❌ Disabled", "inline": True},
                {"name": "Trigger type", "value": type_labels.get(row["trigger_type"], row["trigger_type"]), "inline": True},
                {"name": "Uses", "value": str(row["uses"]), "inline": True},
                {"name": "Trigger", "value": f"`{safe_embed_text(row['trigger_value'])}`", "inline": False},
                {"name": "Response", "value": safe_embed_text(row["response"], max_length=512), "inline": False},
                {
                    "name": "Options",
                    "value": (
                        f"Case sensitive: {'Yes' if row['case_sensitive'] else 'No'} · "
                        f"Delete trigger: {'Yes' if row['delete_trigger'] else 'No'} · "
                        f"Reply: {'Yes' if row['reply_to_user'] else 'No'}"
                    ),
                    "inline": False,
                },
            ],
            footer=f"Created by user ID {row['created_by']} · Use /cc edit {row['name']} to modify",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @cc.command(name="toggle", description="Enable or disable a custom command.")
    @app_commands.describe(name="Name of the command to toggle")
    @requires_admin()
    async def cc_toggle(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        pool = get_bot_db_pool(self.bot)
        if not pool:
            await interaction.followup.send(
                embed=EmbedBuilder.error("Database Unavailable", "Cannot connect to the database."),
                ephemeral=True,
            )
            return

        guild_id = interaction.guild.id

        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                "SELECT id, name, enabled FROM custom_commands WHERE guild_id = $1 AND name = $2",
                guild_id, name.strip().lower(),
            )
            if not row:
                await interaction.followup.send(
                    embed=EmbedBuilder.error("Not Found", f"No command named **{safe_embed_text(name)}** found."),
                    ephemeral=True,
                )
                return

            new_state = not row["enabled"]
            await conn.execute(
                "UPDATE custom_commands SET enabled = $1, updated_at = NOW() WHERE id = $2",
                new_state, row["id"],
            )

        self._invalidate_cache(guild_id)

        label = "enabled" if new_state else "disabled"
        embed = EmbedBuilder.success(
            f"Command {label.capitalize()}",
            f"**{safe_embed_text(row['name'])}** has been {label}.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CustomCommandsCog(bot))
