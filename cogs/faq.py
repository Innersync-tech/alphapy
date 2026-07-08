import re
from typing import Any, cast

import asyncpg
import discord
from asyncpg import exceptions as pg_exceptions
from discord import app_commands
from discord.ext import commands

from utils.database_helpers import DatabaseManager
from utils.db_helpers import acquire_safe, is_pool_healthy
from utils.embed_builder import EmbedBuilder
from utils.logger import logger
from utils.validators import validate_admin

SYNS: dict[str, list[str]] = {
    "pwd": ["password"],
    "pass": ["password"],
    "mail": ["email"],
    "e-mail": ["email"],
    "login": ["signin", "sign-in"],
}


def _normalize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if len(t) >= 3]
    return tokens


def _score_entry(query_tokens: list[str], entry: dict[str, Any]) -> int:
    hay = " ".join([
        entry.get("summary") or "",
        " ".join(entry.get("keywords") or []),
        entry.get("title") or "",
    ])
    tokens = set(_normalize(hay))
    score = 0
    for qt in query_tokens:
        if qt in tokens:
            score += 2  # direct token match has higher weight
        # synonyms
        for base, syns in SYNS.items():
            if qt == base or qt in syns:
                if base in tokens or any(s in tokens for s in syns):
                    score += 1
                    break
    return score


class FAQ(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("faq", bot=bot)
        self.bot.loop.create_task(self._setup_db())

    async def _setup_db(self) -> None:
        try:
            pool = await self._db_manager.ensure_pool()
            async with self._db_manager.connection() as conn:
                await conn.execute("ALTER TABLE faq_entries ADD COLUMN IF NOT EXISTS title TEXT;")
                await conn.execute("ALTER TABLE faq_entries ADD COLUMN IF NOT EXISTS keywords TEXT[];")
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS faq_search_logs (
                      id SERIAL PRIMARY KEY,
                      query TEXT NOT NULL,
                      match_count INT NOT NULL,
                      created_at TIMESTAMPTZ DEFAULT NOW()
                    );
                    """
                )
            self.db = pool
            logger.info("FAQ: DB ready")
        except Exception as e:
            logger.error(f"FAQ: DB init error: {e}")
            self.db = None

    async def cog_unload(self):
        """Called when the cog is unloaded."""
        self.db = None

    async def _fetch_entries(self) -> list[asyncpg.Record]:
        if not is_pool_healthy(self.db):
            return []
        try:
            async with acquire_safe(self.db) as conn:
                rows = await conn.fetch("SELECT id, title, summary, keywords, created_at FROM faq_entries ORDER BY created_at DESC")
                return rows
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            logger.warning(f"Database connection error in _fetch_entries: {conn_err}")
            return []
        except Exception as e:
            logger.error(f"Database error in _fetch_entries: {e}")
            return []

    async def _search_entries(self, query: str, limit: int = 5) -> list[asyncpg.Record]:
        rows = await self._fetch_entries()
        q_tokens = _normalize(query)
        scored = []
        for r in rows:
            score = _score_entry(q_tokens, dict(r))
            if score > 0:
                scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [r for _, r in scored[:limit]]
        # log
        try:
            if is_pool_healthy(self.db):
                async with acquire_safe(self.db) as conn:
                    await conn.execute("INSERT INTO faq_search_logs (query, match_count) VALUES ($1, $2)", query, len(results))
        except Exception:
            pass
        return results

    # --- Slash group
    faq = app_commands.Group(name="faq", description="FAQ commands")

    async def _log_embed(self, title: str, description: str) -> None:
        try:
            channel = self.bot.get_channel(0)  # Must be configured via /config system set_log_channel
            if channel and hasattr(channel, "send"):
                embed = EmbedBuilder.log(title=title, description=description)
                text_channel = cast(discord.TextChannel, channel)
                await text_channel.send(embed=embed)
        except Exception:
            pass

    def _page_embed(self, rows: list[asyncpg.Record], page: int, page_size: int = 10) -> discord.Embed:
        start = page * page_size
        end = start + page_size
        slice_rows = rows[start:end]
        embed = EmbedBuilder.info(title="📚 FAQ – Latest")
        if not slice_rows:
            embed.description = "No FAQ entries yet."
            return embed
        from utils.sanitizer import safe_embed_text
        for r in slice_rows:
            title = r.get("title") or f"Entry #{r['id']}"
            preview = (r.get("summary") or "-")
            if len(preview) > 140:
                preview = preview[:140] + "…"
            embed.add_field(name=f"[{r['id']}] {safe_embed_text(title)}", value=safe_embed_text(preview), inline=False)
        embed.set_footer(text=f"Page {page+1} / {max(1, (len(rows)+page_size-1)//page_size)}")
        return embed

    class FAQListView(discord.ui.View):
        def __init__(self, cog: "FAQ", rows: list[asyncpg.Record], public: bool, page: int = 0, page_size: int = 10):
            super().__init__(timeout=180)
            self.cog = cog
            self.rows = rows
            self.page = page
            self.page_size = page_size
            self.public = public
            self._sync_buttons()

        def _sync_buttons(self) -> None:
            total_pages = max(1, (len(self.rows)+self.page_size-1)//self.page_size)
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    if child.custom_id == "faq_prev":
                        child.disabled = (self.page <= 0)
                    if child.custom_id == "faq_next":
                        child.disabled = (self.page >= total_pages - 1)

        async def _update(self, interaction: discord.Interaction) -> None:
            self._sync_buttons()
            embed = self.cog._page_embed(self.rows, self.page, self.page_size)
            await interaction.response.edit_message(embed=embed, view=self)

        @discord.ui.button(label="⬅ Prev", style=discord.ButtonStyle.secondary, custom_id="faq_prev")
        async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.page > 0:
                self.page -= 1
            await self._update(interaction)

        @discord.ui.button(label="➡ Next", style=discord.ButtonStyle.primary, custom_id="faq_next")
        async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
            total_pages = max(1, (len(self.rows)+self.page_size-1)//self.page_size)
            if self.page < total_pages - 1:
                self.page += 1
            await self._update(interaction)

    @faq.command(name="list", description="Show latest FAQ entries (last 10)")
    @app_commands.describe(public="Post in channel instead of ephemeral (default: false)")
    async def faq_list(self, interaction: discord.Interaction, public: bool = False):
        await interaction.response.defer(ephemeral=not public)
        rows = await self._fetch_entries()
        if not rows:
            await interaction.followup.send("No FAQ entries yet.", ephemeral=not public)
            return
        embed = self._page_embed(rows, page=0, page_size=10)
        if len(rows) > 10:
            view = FAQ.FAQListView(self, rows, public, page=0, page_size=10)
            await interaction.followup.send(embed=embed, view=view, ephemeral=not public)
        else:
            await interaction.followup.send(embed=embed, ephemeral=not public)
        await self._log_embed("📚 FAQ list", f"count={len(rows)} • public={public}")

    @faq.command(name="view", description="View a FAQ entry by ID")
    @app_commands.describe(id="FAQ entry ID", public="Post in channel instead of ephemeral (default: false)")
    async def faq_view(self, interaction: discord.Interaction, id: int, public: bool = False):
        await interaction.response.defer(ephemeral=not public)
        if not is_pool_healthy(self.db):
            await interaction.followup.send("Database not connected.", ephemeral=not public)
            return
        try:
            async with acquire_safe(self.db) as conn:
                row = await conn.fetchrow("SELECT id, title, summary, keywords, created_at FROM faq_entries WHERE id = $1", id)
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            logger.warning(f"Database connection error in faq_view: {conn_err}")
            await interaction.followup.send("❌ Database connection error. Please try again later.", ephemeral=not public)
            return
        except Exception as e:
            logger.error(f"Database error in faq_view: {e}")
            await interaction.followup.send("❌ Database error. Please try again later.", ephemeral=not public)
            return
        if not row:
            await interaction.followup.send("Entry not found.", ephemeral=not public)
            return
        from utils.sanitizer import safe_embed_text
        title = row.get("title") or f"Entry #{row['id']}"
        embed = EmbedBuilder.success(title=f"📖 {safe_embed_text(title)}", description=safe_embed_text(row.get("summary") or "-"))
        kws = row.get("keywords") or []
        if kws:
            embed.add_field(name="Keywords", value=", ".join(kws), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=not public)
        await self._log_embed("📖 FAQ view", f"id={id} • public={public}")

    @faq.command(name="search", description="Search in FAQ entries")
    @app_commands.describe(query="Your question or keywords", public="Post in channel instead of ephemeral (default: false)")
    async def faq_search(self, interaction: discord.Interaction, query: str, public: bool = False):
        await interaction.response.defer(ephemeral=not public)
        results = await self._search_entries(query, limit=5)
        if not results:
            await interaction.followup.send("No results.", ephemeral=not public)
            return
        embed = EmbedBuilder.warning(title="🔎 FAQ – Top results")
        from utils.sanitizer import safe_embed_text
        for r in results:
            title = r.get("title") or f"Entry #{r['id']}"
            preview = (r.get("summary") or "-")
            if len(preview) > 160:
                preview = preview[:160] + "…"
            embed.add_field(name=f"[{r['id']}] {safe_embed_text(title)}", value=safe_embed_text(preview), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=not public)
        await self._log_embed("🔎 FAQ search", f"query=‘{query}’ • matches={len(results)} • public={public}")

    @faq_search.autocomplete("query")
    async def faq_search_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        # quick suggestion based on titles/keywords
        choices: list[app_commands.Choice[str]] = []
        try:
            rows = await self._fetch_entries()
            current_norm = current.lower()
            for r in rows[:25]:
                title = (r.get("title") or "").strip()
                keywords = r.get("keywords") or []
                if (title and current_norm in title.lower()) or any(current_norm in (k or "").lower() for k in keywords):
                    label = title or f"Entry #{r['id']}"
                    choices.append(app_commands.Choice(name=label[:100], value=label))
                    if len(choices) >= 15:
                        break
        except Exception:
            pass
        return choices

    @faq.command(name="reload", description="Reload FAQ index (admin)")
    async def faq_reload(self, interaction: discord.Interaction):
        is_admin, error_msg = await validate_admin(interaction, raise_on_fail=False)
        if not is_admin:
            await interaction.response.send_message(error_msg or "⛔ Admins only.", ephemeral=True)
            return
        await interaction.response.send_message("✅ FAQ index reload queued (in-memory).", ephemeral=True)

    class AddFAQModal(discord.ui.Modal):
        def __init__(self, cog: "FAQ"):
            super().__init__(title="Add FAQ entry")
            self.cog = cog
            self.title_input = discord.ui.TextInput(
                label="Title",
                placeholder="How to reset my password?",
                max_length=100,
            )
            self.summary_input = discord.ui.TextInput(
                label="Summary",
                style=discord.TextStyle.paragraph,
                placeholder="Describe the solution in up to 1000 characters",
                max_length=1000,
            )
            self.keywords_input = discord.ui.TextInput(
                label="Keywords (comma-separated)",
                placeholder="password, reset, login",
                max_length=200,
                required=False,
            )
            self.add_item(self.title_input)
            self.add_item(self.summary_input)
            self.add_item(self.keywords_input)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            title = str(self.title_input.value).strip()
            summary = str(self.summary_input.value).strip()
            kw_raw = str(self.keywords_input.value or "")
            keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]
            if not title or not summary:
                await interaction.response.send_message("❌ Title and summary are required.", ephemeral=True)
                return
            try:
                if not is_pool_healthy(self.cog.db):
                    await interaction.response.send_message("❌ Database not connected.", ephemeral=True)
                    return
                async with acquire_safe(self.cog.db) as conn:
                    row = await conn.fetchrow(
                        "INSERT INTO faq_entries (title, summary, keywords) VALUES ($1, $2, $3) RETURNING id",
                        title,
                        summary,
                        keywords,
                    )
                new_id = row["id"] if row else None
                embed = EmbedBuilder.success(
                    title="✅ FAQ entry created",
                    description=f"ID: `{new_id}`\nTitle: **{title}**"
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                await self.cog._log_embed(
                    "🟢 New FAQ added",
                    f"[{new_id}] {title} by {interaction.user} ({interaction.user.id})",
                )
            except Exception as e:
                logger.error(f"Failed to create FAQ entry: {e}")
                await interaction.response.send_message("❌ Failed to create entry. Please try again.", ephemeral=True)

    @faq.command(name="add", description="Add a new FAQ entry (admin)")
    async def faq_add(self, interaction: discord.Interaction):
        is_admin, error_msg = await validate_admin(interaction, raise_on_fail=False)
        if not is_admin:
            await interaction.response.send_message(error_msg or "⛔ Admins only.", ephemeral=True)
            return
        await interaction.response.send_modal(FAQ.AddFAQModal(self))

    class EditFAQModal(discord.ui.Modal):
        def __init__(self, cog: "FAQ", entry_id: int, row: asyncpg.Record):
            super().__init__(title=f"Edit FAQ #{entry_id}")
            self.cog = cog
            self.entry_id = entry_id
            self.old_title = row.get("title") or f"Entry #{row['id']}"
            self.title_input = discord.ui.TextInput(
                label="Title",
                max_length=100,
                default=str(row.get("title") or "").strip(),
            )
            self.summary_input = discord.ui.TextInput(
                label="Summary",
                style=discord.TextStyle.paragraph,
                max_length=1000,
                default=str(row.get("summary") or "").strip(),
            )
            kws = row.get("keywords") or []
            kws_str = ", ".join([str(k) for k in kws])
            self.keywords_input = discord.ui.TextInput(
                label="Keywords (comma-separated)",
                max_length=200,
                required=False,
                default=kws_str,
            )
            self.add_item(self.title_input)
            self.add_item(self.summary_input)
            self.add_item(self.keywords_input)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            title = str(self.title_input.value).strip()
            summary = str(self.summary_input.value).strip()
            kw_raw = str(self.keywords_input.value or "")
            keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]
            if not title or not summary:
                await interaction.response.send_message("❌ Title and summary are required.", ephemeral=True)
                return
            try:
                if not is_pool_healthy(self.cog.db):
                    await interaction.response.send_message("❌ Database not connected.", ephemeral=True)
                    return
                async with acquire_safe(self.cog.db) as conn:
                    await conn.execute(
                        "UPDATE faq_entries SET title=$1, summary=$2, keywords=$3 WHERE id=$4",
                        title,
                        summary,
                        keywords,
                        int(self.entry_id),
                    )
                embed = EmbedBuilder.success(
                    title="✅ FAQ entry updated",
                    description=f"ID: `{self.entry_id}`\nTitle: **{title}**"
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                await self.cog._log_embed(
                    "🟡 FAQ edited",
                    f"id={self.entry_id} • {self.old_title} → {title} • by {interaction.user} ({interaction.user.id})",
                )
            except Exception as e:
                logger.error(f"Failed to update FAQ entry: {e}")
                await interaction.response.send_message("❌ Failed to update entry. Please try again.", ephemeral=True)

    @faq.command(name="edit", description="Edit a FAQ entry (admin)")
    @app_commands.describe(id="FAQ entry ID")
    async def faq_edit(self, interaction: discord.Interaction, id: int):
        is_admin, error_msg = await validate_admin(interaction, raise_on_fail=False)
        if not is_admin:
            await interaction.response.send_message(error_msg or "⛔ Admins only.", ephemeral=True)
            return
        if not is_pool_healthy(self.db):
            await interaction.response.send_message("❌ Database not connected.", ephemeral=True)
            return
        try:
            async with acquire_safe(self.db) as conn:
                row = await conn.fetchrow("SELECT id, title, summary, keywords FROM faq_entries WHERE id = $1", id)
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            logger.warning(f"Database connection error in faq_edit: {conn_err}")
            await interaction.response.send_message("❌ Database connection error. Please try again later.", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"Database error in faq_edit: {e}")
            await interaction.response.send_message("❌ Database error. Please try again later.", ephemeral=True)
            return
        if not row:
            await interaction.response.send_message("❌ Entry not found.", ephemeral=True)
            return
        await interaction.response.send_modal(FAQ.EditFAQModal(self, id, row))


async def setup(bot: commands.Bot):
    await bot.add_cog(FAQ(bot))


