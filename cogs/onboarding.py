import asyncio
import json
import re
import uuid
from typing import Any, Optional, cast

import asyncpg
import discord
from asyncpg import exceptions as pg_exceptions
from discord.ext import commands

import config
from utils.database_helpers import DatabaseManager
from utils.db_helpers import acquire_safe, get_bot_db_pool, is_pool_healthy
from utils.embed_builder import EmbedBuilder
from utils.logger import logger
from utils.operational_logs import EventType, log_operational_event


class Onboarding(commands.Cog):
    """Cog that manages the onboarding process for new users."""
    
    def __init__(self, bot):
        self.bot = bot
        # Track current step and answers per user.
        self.active_sessions = {}  # { user_id: {"step": int, "answers": {}} }
        # Regex for email validation
        self.EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w{2,}$")

        # Default questions (used when no custom questions are configured for a guild)
        self.default_questions = [
            {
                "question": "📣 How did you find our community?",
                "options": [
                    ("Invited by a member", "friend"),
                    ("Social Media", "social"),
                    ("Online Search", "search"),
                    ("Other", "other")
                ],
                "followup": {
                    "friend": {"question": "Who invited you?"},
                    "social": {"question": "Which platform?"},
                    "search": {"question": "What did you search for?"},
                    "other": {"question": "How did you find us?"}
                }
            },
            {
                "question": "🎯 What brings you to our community?",
                "options": [
                    ("Learn new skills", "learning"),
                    ("Network with like-minded people", "networking"),
                    ("Share knowledge & experience", "sharing"),
                    ("Find opportunities", "opportunities"),
                    ("Other", "other")
                ],
                "multiple": True
            },
            {
                "question": "💬 How would you like to connect with the community?",
                "options": [
                    ("Join discussions in channels", "discussions"),
                    ("Attend community events", "events"),
                    ("One-on-one conversations", "personal"),
                    ("Just observe for now", "observe")
                ],
                "multiple": True
            },
            {
                "question": "📧 What's your email address? (Optional)",
                "type": "email",
                "optional": True
            }
        ]

        # Cache for guild questions (guild_id -> questions list)
        self.guild_questions_cache = {}
        # Cache for guild rules (guild_id -> rules list)
        self.guild_rules_cache = {}

        # Personalization: synthetic steps after guild questions (opt-in + optional language)
        self.NUM_PERSONALIZATION_STEPS = 2
        self.PERSONALIZATION_OPT_IN_QUESTION = "Would you like to receive personalized reminders and tips (based on your answers)?"
        self.PERSONALIZATION_OPT_IN_OPTIONS = [
            ("Yes, please!", "full"),
            ("Only for events and sessions", "events_only"),
            ("No, thanks", "no"),
        ]
        self.PERSONALIZATION_OPT_IN_LABELS = {"full": "Yes, please!", "events_only": "Only for events and sessions", "no": "No, thanks"}
        self.PERSONALIZATION_LANGUAGE_QUESTION = "In which language would you like to receive personalized reminders and tips?"
        self.PERSONALIZATION_LANGUAGE_OPTIONS = [
            ("Nederlands", "nl"),
            ("English", "en"),
            ("Español", "es"),
            ("Français", "fr"),
            ("Deutsch", "de"),
            ("Other language…", "other"),
        ]

        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("onboarding", bot=bot)

    async def get_guild_questions(self, guild_id: int) -> list:
        """Load questions for a specific guild from database, or use defaults if none configured."""
        # Check cache first
        if guild_id in self.guild_questions_cache:
            return self.guild_questions_cache[guild_id]

        if not await self._ensure_pool():
            logger.warning(f"Database not available, using default questions for guild {guild_id}")
            return self.default_questions

        try:
            if self.db is None:
                logger.error("Database pool is None")
                return self.default_questions
            async with acquire_safe(self.db) as conn:
                rows = await conn.fetch("""
                    SELECT question, question_type, options, followup, required
                    FROM guild_onboarding_questions
                    WHERE guild_id = $1 AND enabled = TRUE
                    ORDER BY step_order
                """, guild_id)

                if rows:
                    questions = []
                    for row in rows:
                        question_data = {
                            "question": row["question"],
                            "type": row["question_type"] if row["question_type"] in ["email", "text"] else None,
                            "optional": not row["required"]
                        }

                        if row["question_type"] in ["select", "multiselect"]:
                            if row["options"]:
                                # Convert JSONB options to tuple format expected by the code
                                question_data["options"] = [
                                    (opt["label"], opt["value"]) for opt in row["options"]
                                ]
                            if row["question_type"] == "multiselect":
                                question_data["multiple"] = True
                            if row["followup"]:
                                question_data["followup"] = row["followup"]

                        questions.append(question_data)

                    self.guild_questions_cache[guild_id] = questions
                    return questions
                else:
                    # No custom questions configured, use defaults
                    self.guild_questions_cache[guild_id] = self.default_questions
                    return self.default_questions

        except Exception as e:
            logger.error(f"Failed to load questions for guild {guild_id}: {e}")
            return self.default_questions

    async def save_guild_question(self, guild_id: int, step_order: int, question_data: dict) -> bool:
        """Save a question for a specific guild."""
        if not await self._ensure_pool():
            return False

        try:
            if self.db is None:
                logger.error("Database pool is None")
                return False
            async with acquire_safe(self.db) as conn:
                # Convert options from tuple format to JSONB
                options_json = None
                if "options" in question_data and question_data["options"]:
                    options_json = [
                        {"label": label, "value": value}
                        for label, value in question_data["options"]
                    ]

                followup_json = question_data.get("followup")

                await conn.execute("""
                    INSERT INTO guild_onboarding_questions
                    (guild_id, step_order, question, question_type, options, followup, required, enabled)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)
                    ON CONFLICT (guild_id, step_order)
                    DO UPDATE SET
                        question = EXCLUDED.question,
                        question_type = EXCLUDED.question_type,
                        options = EXCLUDED.options,
                        followup = EXCLUDED.followup,
                        required = EXCLUDED.required,
                        updated_at = CURRENT_TIMESTAMP
                """,
                guild_id,
                step_order,
                question_data["question"],
                question_data.get("type", "select") if question_data.get("type") else
                ("multiselect" if question_data.get("multiple") else "select"),
                options_json,
                followup_json,
                not question_data.get("optional", False)
                )

                # Clear cache for this guild
                if guild_id in self.guild_questions_cache:
                    del self.guild_questions_cache[guild_id]

                return True

        except Exception as e:
            logger.error(f"Failed to save question for guild {guild_id}: {e}")
            return False

    async def delete_guild_question(self, guild_id: int, step_order: int) -> bool:
        """Delete a question for a specific guild."""
        if not await self._ensure_pool():
            return False

        try:
            if self.db is None:
                logger.error("Database pool is None")
                return False
            async with acquire_safe(self.db) as conn:
                await conn.execute("""
                    DELETE FROM guild_onboarding_questions
                    WHERE guild_id = $1 AND step_order = $2
                """, guild_id, step_order)

                # Clear cache for this guild
                if guild_id in self.guild_questions_cache:
                    del self.guild_questions_cache[guild_id]

                return True

        except Exception as e:
            logger.error(f"Failed to delete question for guild {guild_id}: {e}")
            return False

    async def get_guild_rules(self, guild_id: int) -> list:
        """Load rules for a specific guild from database. Returns empty list if none configured."""
        # Check cache first
        if guild_id in self.guild_rules_cache:
            return self.guild_rules_cache[guild_id]

        if not await self._ensure_pool():
            logger.warning(f"Database not available for guild {guild_id}")
            return []

        try:
            if self.db is None:
                logger.error("Database pool is None")
                return []
            async with acquire_safe(self.db) as conn:
                rows = await conn.fetch("""
                    SELECT title, description, thumbnail_url, image_url
                    FROM guild_rules
                    WHERE guild_id = $1 AND enabled = TRUE
                    ORDER BY rule_order
                """, guild_id)

                if rows:
                    rules = [
                        {
                            "title": row["title"],
                            "description": row["description"],
                            "thumbnail_url": row.get("thumbnail_url") or None,
                            "image_url": row.get("image_url") or None,
                        }
                        for row in rows
                    ]
                    self.guild_rules_cache[guild_id] = rules
                    return rules
                else:
                    # Do not cache empty list: rules added via dashboard API must
                    # be visible on next fetch without requiring cache invalidation.
                    return []

        except Exception as e:
            logger.error(f"Failed to load rules for guild {guild_id}: {e}")
            return []

    async def save_guild_rule(
        self,
        guild_id: int,
        rule_order: int,
        title: str,
        description: str,
        thumbnail_url: str | None = None,
        image_url: str | None = None,
    ) -> bool:
        """Save a rule for a specific guild."""
        if not await self._ensure_pool():
            return False

        try:
            if self.db is None:
                logger.error("Database pool is None")
                return False
            async with acquire_safe(self.db) as conn:
                await conn.execute("""
                    INSERT INTO guild_rules
                    (guild_id, rule_order, title, description, thumbnail_url, image_url, enabled)
                    VALUES ($1, $2, $3, $4, $5, $6, TRUE)
                    ON CONFLICT (guild_id, rule_order)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        description = EXCLUDED.description,
                        thumbnail_url = EXCLUDED.thumbnail_url,
                        image_url = EXCLUDED.image_url,
                        updated_at = CURRENT_TIMESTAMP
                """,
                guild_id,
                rule_order,
                title,
                description,
                thumbnail_url or None,
                image_url or None,
                )

                # Clear cache for this guild
                if guild_id in self.guild_rules_cache:
                    del self.guild_rules_cache[guild_id]

                return True

        except Exception as e:
            logger.error(f"Failed to save rule for guild {guild_id}: {e}")
            return False

    async def delete_guild_rule(self, guild_id: int, rule_order: int) -> bool:
        """Delete a rule for a specific guild."""
        if not await self._ensure_pool():
            return False

        try:
            if self.db is None:
                logger.error("Database pool is None")
                return False
            async with acquire_safe(self.db) as conn:
                await conn.execute("""
                    DELETE FROM guild_rules
                    WHERE guild_id = $1 AND rule_order = $2
                """, guild_id, rule_order)

                # Clear cache for this guild
                if guild_id in self.guild_rules_cache:
                    del self.guild_rules_cache[guild_id]

                return True

        except Exception as e:
            logger.error(f"Failed to delete rule for guild {guild_id}: {e}")
            return False

    def _value_to_label(self, q_data: dict, value: object) -> str:
        options = q_data.get("options")
        if not isinstance(options, list):
            return str(value)
        for label, val in options:
            if val == value:
                return label
        return str(value)

    def _format_answer(self, q_data: dict, raw_answer: object) -> str:
        if isinstance(raw_answer, dict):
            choice_value = raw_answer.get("choice")
            choice_label = self._value_to_label(q_data, choice_value)
            followup_text = raw_answer.get("followup")
            followup_label = raw_answer.get("followup_label") or "Details"
            return f"{choice_label} — {followup_label}: {followup_text}" if followup_text else f"{choice_label}"
        if isinstance(raw_answer, list):
            mapped = [self._value_to_label(q_data, v) for v in raw_answer]
            return ", ".join(mapped) if mapped else "No response"
        return self._value_to_label(q_data, raw_answer)

    async def setup_database(self):
        """Initialiseer de PostgreSQL database en maak tabellen aan indien nodig."""
        await self._ensure_pool()

    async def _connect_pool(self) -> None:
        pool = get_bot_db_pool(self.bot)
        if not is_pool_healthy(pool):
            raise RuntimeError("Shared database pool not available")
        async with acquire_safe(pool) as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS onboarding (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    responses JSONB,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(guild_id, user_id)
                );
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS guild_onboarding_questions (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    step_order INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    question_type TEXT NOT NULL DEFAULT 'select', -- 'select', 'multiselect', 'text', 'email'
                    options JSONB, -- For select/multiselect: [{"label": "Option 1", "value": "value1"}, ...]
                    followup JSONB, -- For conditional followups: {"value": {"question": "Followup question"}}
                    required BOOLEAN DEFAULT TRUE,
                    enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, step_order)
                );
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS guild_rules (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    rule_order INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    thumbnail_url TEXT,
                    image_url TEXT,
                    enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, rule_order)
                );
            ''')
            await conn.execute('''
                ALTER TABLE guild_rules ADD COLUMN IF NOT EXISTS thumbnail_url TEXT;
            ''')
            await conn.execute('''
                ALTER TABLE guild_rules ADD COLUMN IF NOT EXISTS image_url TEXT;
            ''')
        self.db = pool
        logger.info("✅ Onboarding: DB pool ready")

    async def _ensure_pool(self, *, attempts: int = 3, base_delay: float = 2.0) -> bool:
        if self.db is not None:
            return True

        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await self._connect_pool()
                return True
            except (pg_exceptions.PostgresError, ConnectionError, OSError) as exc:
                last_error = exc
                logger.warning(
                    f"⚠️ Onboarding: DB connect failed (attempt {attempt}/{attempts}): {exc}"
                )
                await asyncio.sleep(base_delay * attempt)
            except Exception as exc:
                last_error = exc
                logger.exception("❌ Onboarding: unexpected DB-init error")
                break

        logger.error(f"❌ Onboarding: could not establish DB connection: {last_error}")
        return False

    async def _show_onboarding_step(
        self, interaction: discord.Interaction, session: dict | None, embed: discord.Embed, view: discord.ui.View
    ) -> None:
        """
        Show the next onboarding step (embed + view) by editing the current message when possible,
        otherwise sending a followup. Uses session['onboarding_message'] when interaction.message is
        None (e.g. after modal submit) so we keep updating the same message.
        """
        msg = interaction.message or (session.get("onboarding_message") if session else None)
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(content="", embed=embed, view=view)
                if session is not None and interaction.message is not None:
                    session["onboarding_message"] = interaction.message
                return
        except discord.errors.InteractionResponded:
            pass
        if msg is not None and getattr(msg, "edit", None):
            try:
                await msg.edit(content="", embed=embed, view=view)
                if session is not None:
                    session["onboarding_message"] = msg
                return
            except (discord.NotFound, discord.Forbidden):
                pass
        # After defer or when message edit failed: try to edit the original response so we
        # don't send a new followup (e.g. first step from rules, or after modal submit).
        try:
            await interaction.edit_original_response(content="", embed=embed, view=view)
            return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        sent = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
        if sent is not None and session is not None:
            session["onboarding_message"] = sent

    async def send_next_question(self, interaction: discord.Interaction, step: int = 0, answers: dict | None = None):
        user_id = interaction.user.id
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        guild_id = interaction.guild.id

        # Ensure there's an active session for this user
        if user_id not in self.active_sessions:
            self.active_sessions[user_id] = {"step": 0, "answers": {}}
        session = self.active_sessions[user_id]

        # Use existing answers or update the session
        if answers is None:
            answers = session["answers"]
        else:
            session["answers"] = answers

        # Get questions for this guild
        questions = await self.get_guild_questions(guild_id)
        n_questions = len(questions)
        completion_step = n_questions + self.NUM_PERSONALIZATION_STEPS

        # If all questions and personalization steps are done, process completion
        if step >= completion_step:
            logger.info(f"🎉 Onboarding completed for {interaction.user.display_name}!")

            # Edit the form message to remove components (dropdown/buttons) so later clicks have no effect
            done_embed = EmbedBuilder.success(
                title="✅ Onboarding complete",
                description="Your responses have been saved. See below for your summary."
            )
            for msg in (interaction.message, session.get("onboarding_message") if session else None):
                if msg is None or not getattr(msg, "edit", None):
                    continue
                try:
                    await msg.edit(content="", embed=done_embed, view=None)
                    break
                except (discord.NotFound, discord.Forbidden) as e:
                    logger.debug("Could not edit message on completion (e.g. ephemeral from rules): %s", e)
                except Exception as e:
                    logger.warning(f"Could not edit onboarding message on completion: {e}")

            summary_embed = EmbedBuilder.info(
                title="📜 Onboarding Summary",
                description=f"Here is a summary of your onboarding responses, {interaction.user.display_name}:"
            )
            from utils.sanitizer import safe_embed_text
            for idx, question in enumerate(questions):
                raw_answer = (answers or {}).get(idx, "No response")
                answer_text = self._format_answer(question, raw_answer)
                summary_embed.add_field(name=f"**{safe_embed_text(question['question'])}**", value=f"➜ {safe_embed_text(answer_text)}", inline=False)
            opt_in = (answers or {}).get("personalized_opt_in")
            if opt_in is not None:
                opt_in_label = self.PERSONALIZATION_OPT_IN_LABELS.get(opt_in, str(opt_in))
                summary_embed.add_field(name="**Personalized reminders**", value=f"➜ {safe_embed_text(opt_in_label, 1024)}", inline=False)
            pref_lang = (answers or {}).get("preferred_language")
            if pref_lang is not None:
                summary_embed.add_field(name="**Preferred language**", value=f"➜ {safe_embed_text(str(pref_lang), 1024)}", inline=False)

            # Premium: show benefit for premium users or upgrade CTA for others
            view = None
            try:
                from utils.premium_guard import is_premium
                premium = await is_premium(user_id, guild_id)
                if premium:
                    summary_embed.add_field(
                        name="✨ Premium",
                        value="You have access to image reminders and Mockingbird mode in growth check-ins.",
                        inline=False,
                    )
                else:
                    checkout_url = getattr(config, "PREMIUM_CHECKOUT_URL", "") or ""
                    if checkout_url:
                        view = discord.ui.View()
                        view.add_item(
                            discord.ui.Button(
                                label="Upgrade to Premium",
                                url=checkout_url,
                                style=discord.ButtonStyle.link,
                            )
                        )
            except Exception as e:
                logger.debug("Onboarding: premium check failed (non-critical): %s", e)

            # Send summary to user
            send_kwargs = {"embed": summary_embed, "ephemeral": True}
            if view is not None:
                send_kwargs["view"] = view
            if not interaction.response.is_done():
                await interaction.response.send_message(**send_kwargs)
            else:
                await interaction.followup.send(**send_kwargs)

            # Save the onboarding data to the database
            stored = await self.store_onboarding_data(interaction.guild.id, user_id, answers)
            if not stored:
                await interaction.followup.send(
                    "⚠️ Onboarding data could not be saved. Please try again later or contact an admin.",
                    ephemeral=True
                )

            # Assign completion role if set
            completion_role_id = self.bot.settings.get("onboarding", "completion_role_id", interaction.guild.id)
            if completion_role_id and completion_role_id != 0:
                try:
                    role = interaction.guild.get_role(completion_role_id)
                    # Resolve member: interaction.user may already be Member; get_member uses cache and can return None
                    member = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.get_member(interaction.user.id)
                    if member is None:
                        member = await interaction.guild.fetch_member(interaction.user.id)
                    if role and member and not any(r.id == completion_role_id for r in member.roles):
                        await member.add_roles(role)
                        logger.info(f"✅ Completion role {role.name} assigned to {interaction.user.display_name}")
                        await interaction.followup.send(
                            f"🎉 **Welcome to the server!** You have been assigned the {role.mention} role.",
                            ephemeral=True
                        )
                    elif role and member and any(r.id == completion_role_id for r in member.roles):
                        logger.debug(f"User {interaction.user.display_name} already has completion role")
                    elif not member:
                        logger.warning(f"⚠️ Could not resolve member {interaction.user.id} for role assignment")
                        log_operational_event(
                            EventType.ONBOARDING_ERROR,
                            f"Could not resolve member {interaction.user.id} for role assignment",
                            guild_id=guild_id,
                            details={"user_id": interaction.user.id, "error_type": "member_not_found"}
                        )
                except Exception as e:
                    logger.error(f"⚠️ Could not assign completion role: {e}")
                    log_operational_event(
                        EventType.ONBOARDING_ERROR,
                        f"Failed to assign completion role: {e}",
                        guild_id=guild_id,
                        details={
                            "user_id": interaction.user.id,
                            "role_id": completion_role_id,
                            "error_type": "role_assignment_failed",
                            "error": str(e)
                        }
                    )

            # Always remove join role after onboarding completion, if configured
            try:
                join_role_id = self.bot.settings.get("onboarding", "join_role_id", guild_id)
            except Exception:
                join_role_id = 0
            if join_role_id and join_role_id != 0:
                try:
                    join_role = interaction.guild.get_role(int(join_role_id))
                    member = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.get_member(interaction.user.id)
                    if member is None:
                        member = await interaction.guild.fetch_member(interaction.user.id)
                    if join_role and member and any(r.id == join_role.id for r in member.roles):
                        await member.remove_roles(join_role, reason="Replace join role with onboarding completion role")
                        logger.info(f"Join role {join_role.name} removed from {interaction.user.display_name} after onboarding completion")
                except Exception as e:
                    logger.warning(f"Onboarding: could not remove join role after completion: {e}")

            # Build and send a log embed to the log channel
            log_embed = EmbedBuilder.success(
                title="📝 Onboarding Log",
                description=f"**User:** {interaction.user} ({interaction.user.id})"
            )
            from utils.sanitizer import safe_embed_text
            for idx, question in enumerate(questions):
                raw_answer = (answers or {}).get(idx, "No response")
                answer_text = self._format_answer(question, raw_answer)
                log_embed.add_field(name=safe_embed_text(question['question']), value=f"➜ {safe_embed_text(answer_text)}", inline=False)
            if opt_in is not None:
                opt_in_label = self.PERSONALIZATION_OPT_IN_LABELS.get(opt_in, str(opt_in))
                log_embed.add_field(name="Personalized reminders", value=f"➜ {safe_embed_text(opt_in_label, 1024)}", inline=False)
            if pref_lang is not None:
                log_embed.add_field(name="Preferred language", value=f"➜ {safe_embed_text(str(pref_lang), 1024)}", inline=False)

            log_channel_id = self.bot.settings.get("system", "log_channel_id", interaction.guild.id)
            log_channel = self.bot.get_channel(log_channel_id) if log_channel_id else None
            if log_channel:
                await log_channel.send(embed=log_embed)

            from utils.fyi_tips import send_fyi_if_first
            await send_fyi_if_first(self.bot, interaction.guild.id, "first_onboarding_done")

            return

        if step == n_questions:
            # Personalization opt-in step
            embed = EmbedBuilder.info(
                title="📝 Onboarding Form",
                description=self.PERSONALIZATION_OPT_IN_QUESTION,
                footer="Complete the steps to finish onboarding.",
            )
            view = PersonalizationOptInView(onboarding=self, answers=answers or {}, n_questions=n_questions)
            await self._show_onboarding_step(interaction, session, embed, view)
            return

        if step == n_questions + 1:
            # Personalization language step
            embed = EmbedBuilder.info(
                title="📝 Onboarding Form",
                description=self.PERSONALIZATION_LANGUAGE_QUESTION,
                footer="Complete the steps to finish onboarding.",
            )
            view = PersonalizationLanguageView(onboarding=self, answers=answers or {}, n_questions=n_questions)
            await self._show_onboarding_step(interaction, session, embed, view)
            return

        # Get the current question (guild question)
        q_data = questions[step]

        # If this question requires free text input, send a modal.
        # When coming from a modal submit the interaction is already responded (deferred),
        # so we cannot call send_modal — Discord forbids modal→modal chaining (type 9 is
        # not a valid response to interaction type 5). Use TextInputTriggerView instead.
        if q_data.get("input") or q_data.get("type") in ["email", "text"]:
            modal = TextInputModal(
                title=q_data["question"],
                step=step,
                answers=answers or {},
                onboarding=self,
                optional=q_data.get("optional", False)
            )
            if interaction.response.is_done():
                embed = EmbedBuilder.info(title="📝 Onboarding Form", description=q_data["question"])
                trigger_view = TextInputTriggerView(modal=modal)
                await self._show_onboarding_step(interaction, session, embed, trigger_view)
            else:
                await interaction.response.send_modal(modal)
            return

        # Build embed and view for the question with options
        embed = EmbedBuilder.info(title="📝 Onboarding Form", description=q_data["question"])
        view = OnboardingView(step=step, answers=answers or {}, onboarding=self)

        if q_data.get("multiple"):
            # Multi-select: show only the select; no confirm button needed
            if "options" in q_data:
                view.add_item(OnboardingSelect(step=step, options=q_data["options"], onboarding=self, view_id=view.view_id))
        else:
            # Add buttons for single-select questions
            if "options" in q_data:
                for label, value in q_data["options"]:
                    view.add_item(OnboardingButton(label=label, value=value, step=step, onboarding=self))

        # Confirm button only for single-select questions
        if not q_data.get("multiple"):
            confirm_button = ConfirmButton(step, answers or {}, self)
            confirm_button.disabled = True
            view.add_item(confirm_button)

        await self._show_onboarding_step(interaction, session, embed, view)

    async def store_onboarding_data(self, guild_id: int, user_id, responses) -> bool:
        """Saves onboarding data to the database."""
        if not await self._ensure_pool():
            logger.error("❌ Onboarding: database not available, data not saved")
            return False

        assert self.db is not None
        try:
            if self.db is None:
                logger.error("Database pool is None")
                return False
            async with acquire_safe(self.db) as conn:
                # First try to update existing record for this guild+user combination
                result = await conn.fetchrow(
                    "UPDATE onboarding SET responses = $3 WHERE guild_id = $1 AND user_id = $2 RETURNING user_id",
                    guild_id, user_id, json.dumps(responses)
                )

                if not result:
                    # No existing record found, insert new one
                    try:
                        await conn.execute(
                            """
                            INSERT INTO onboarding (guild_id, user_id, responses)
                            VALUES ($1, $2, $3)
                            """,
                            guild_id, user_id, json.dumps(responses)
                        )
                    except Exception as insert_exc:
                        # If insert fails due to old user_id constraint, try updating by user_id only
                        if "onboarding_user_id_key" in str(insert_exc):
                            logger.warning(f"⚠️ Fallback: updating existing record by user_id only for {user_id}")
                            await conn.execute(
                                "UPDATE onboarding SET responses = $2, guild_id = $3 WHERE user_id = $1",
                                user_id, json.dumps(responses), guild_id
                            )
                        else:
                            raise insert_exc
            logger.info(f"✅ Onboarding data saved for {user_id}")
            return True
        except Exception as exc:
            logger.exception(f"❌ Onboarding: save failed for {user_id}: {exc}")
            return False

    async def get_user_personalization(self, user_id: int, guild_id: int) -> dict[str, Any]:
        """
        Get the user's personalization preferences from their most recent onboarding (opt-in and language).
        Graceful fallback: returns defaults if no record or DB unavailable.

        Returns:
            dict: ``{"opt_in": "full" | "events_only" | "no" | None, "language": str}``.
            - ``opt_in``: Value of ``personalized_opt_in`` from onboarding responses, or None if missing.
            - ``language``: Value of ``preferred_language`` (e.g. ``"nl"``, ``"en"``, ``"other: Italiano"``),
              or ``"en"`` if missing. For "other" the full string is returned for use in prompts.
        """
        default = {"opt_in": None, "language": "en"}
        if not await self._ensure_pool() or self.db is None:
            return default
        try:
            async with acquire_safe(self.db) as conn:
                row = await conn.fetchrow(
                    "SELECT responses FROM onboarding WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    user_id,
                )
            if not row or not row.get("responses"):
                return default
            responses = row["responses"]
            if isinstance(responses, str):
                responses = json.loads(responses)
            opt_in = responses.get("personalized_opt_in") if isinstance(responses, dict) else None
            language = responses.get("preferred_language") if isinstance(responses, dict) else None
            return {
                "opt_in": opt_in,
                "language": language if language else "en",
            }
        except Exception as exc:
            logger.warning(f"get_user_personalization failed for user {user_id} guild {guild_id}: {exc}")
            return default

class TextInputTriggerView(discord.ui.View):
    """Bridge view: shown when Discord forbids responding to a modal with another modal.

    Discord interaction type 5 (modal submit) cannot be answered with interaction
    response type 9 (modal). When a text-input question follows a text-input question,
    this view shows a single button. Clicking it issues a fresh component interaction
    that CAN open the next modal.
    """

    def __init__(self, modal: "TextInputModal"):
        super().__init__(timeout=600)
        self.modal = modal
        btn = discord.ui.Button(
            label="✏️ Type Answer",
            style=discord.ButtonStyle.primary,
        )
        btn.callback = self._open_modal
        self.add_item(btn)

    async def _open_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(self.modal)


class TextInputModal(discord.ui.Modal):
    def __init__(self, title: str, step: int, answers: dict, onboarding: 'Onboarding', optional: bool = False):
        # Discord limits: modal title ≤ 45 chars, TextInput label ≤ 45 chars
        _title = title[:42] + "…" if len(title) > 45 else title
        super().__init__(title=_title)
        self.step = step
        self.answers = answers
        self.onboarding = onboarding
        self.optional = optional

        # Add a text input field. You can add extra validation here if needed.
        placeholder = "Type your answer here..." if not optional else "Type your answer here (or leave empty to skip)..."
        self.input_field = discord.ui.TextInput(
            label=_title,
            placeholder=placeholder,
            style=discord.TextStyle.short,
            required=not optional  # Make field required only if not optional
        )
        self.add_item(self.input_field)

    async def on_submit(self, interaction: discord.Interaction):
        # Store the entered value in the active session
        self.answers[self.step] = self.input_field.value
        user_id = interaction.user.id
        if user_id in self.onboarding.active_sessions:
            self.onboarding.active_sessions[user_id]["answers"][self.step] = self.input_field.value

        # Defer so _show_onboarding_step can edit the session message.
        # send_next_question detects interaction.response.is_done() and uses the
        # TextInputTriggerView bridge instead of send_modal when the next step is
        # also a text-input (Discord forbids modal→modal chaining).
        await interaction.response.defer(ephemeral=True)
        await self.onboarding.send_next_question(interaction, step=self.step + 1, answers=self.answers)


class OnboardingView(discord.ui.View):
    """View for the onboarding flow."""
    def __init__(self, step: int | None = None, answers: dict | None = None, onboarding: Optional['Onboarding'] = None):
        super().__init__(timeout=None)
        self.step = step
        self.answers = answers if answers is not None else {}
        self.onboarding = onboarding
        self.view_id = uuid.uuid4().hex  # Unique ID for this view

class OnboardingButton(discord.ui.Button):
    """Button for single-select answers."""
    def __init__(self, label: str, value: str, step: int, onboarding: Onboarding):
        # Use the unique view_id in the custom_id
        # Note: 'self' is not available yet here, so we need to get this later via the view.
        # Therefore we make the custom_id dynamic in the callback or determine it in the OnboardingView.
        # One way is to set a placeholder in __init__ and override it afterwards.
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.value = value
        self.step = step
        self.onboarding = onboarding

    async def callback(self, interaction: discord.Interaction):
        # We use the view data here, which has a unique view_id.
        logger.info(f"🔘 Button clicked: {self.label} (value: {self.value})")
        user_id = interaction.user.id
        guild_id = interaction.guild.id if interaction.guild else 0
        questions = await self.onboarding.get_guild_questions(guild_id)
        question_data = questions[self.step]
        
        # Single-select: set the answer; if there is a follow-up for this choice, store choice and follow-up in a dict
        view = self.view
        if view is None:
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send("⚠️ Something went wrong. Please try again.", ephemeral=True)
            return
        onboarding_view = cast(OnboardingView, view)
        if self.value in question_data.get("followup", {}):
            onboarding_view.answers[self.step] = {"choice": self.value}
        else:
            onboarding_view.answers[self.step] = self.value
        if user_id in self.onboarding.active_sessions:
            self.onboarding.active_sessions[user_id]["answers"][self.step] = self.value

        # Check if a follow-up modal is needed
        if self.value in question_data.get("followup", {}):
            followup_cfg = question_data["followup"][self.value]
            followup_text = followup_cfg["question"] if isinstance(followup_cfg, dict) else str(followup_cfg)
            validate_email = isinstance(followup_cfg, dict) and followup_cfg.get("type") == "email"
            logger.info(f"📝 Follow-up triggered: {followup_text}")
            await interaction.response.send_modal(
                FollowupModal(
                    title="Follow-up Question",
                    question=followup_text,
                    step=self.step,
                    answers=onboarding_view.answers,
                    validate_email=validate_email,
                    onboarding=self.onboarding
                )
            )
            return

        # Enable the confirm button
        for child in onboarding_view.children:
            if isinstance(child, ConfirmButton):
                child.disabled = False
                break
        await interaction.response.edit_message(view=onboarding_view)


class OnboardingSelect(discord.ui.Select):
    """Select menu for multi-select questions."""
    def __init__(self, step: int, options: list, onboarding: Onboarding, view_id: str):
        select_options = []
        for label, value in options:
            select_options.append(discord.SelectOption(label=label, value=value))
        # Build a unique custom_id using the view_id and step
        custom_id = f"onboarding_select_{step}_{view_id}"
        super().__init__(
            placeholder="Select one or more options...",
            min_values=1,
            max_values=len(options),
            options=select_options,
            custom_id=custom_id
        )
        self.step = step
        self.onboarding = onboarding

    async def callback(self, interaction: discord.Interaction):
        logger.info(f"🔘 Select callback: selected values: {self.values}")
        user_id = interaction.user.id
        view = self.view
        if view is None:
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send("⚠️ Something went wrong. Please try again.", ephemeral=True)
            return
        onboarding_view = cast(OnboardingView, view)
        onboarding_view.answers[self.step] = self.values
        if user_id in self.onboarding.active_sessions:
            self.onboarding.active_sessions[user_id]["answers"][self.step] = self.values
        # Go directly to the next question for multi-select (no confirm needed)
        await self.onboarding.send_next_question(interaction, step=self.step + 1, answers=onboarding_view.answers)


class ConfirmButton(discord.ui.Button):
    """Button to confirm the current step and proceed."""
    def __init__(self, step: int, answers: dict, onboarding: Onboarding):
        # Create a unique custom_id using the view_id; we adjust this in the view.
        # Since we don't know 'self.view' yet in __init__, you can choose to
        # override or generate the custom_id later in the view.
        super().__init__(label="✅ Confirm", style=discord.ButtonStyle.success)
        self.step = step
        self.answers = answers
        self.onboarding = onboarding

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        if user_id not in self.onboarding.active_sessions:
            logger.warning(f"⚠️ No active session for {interaction.user.display_name}.")
            return
        logger.info(f'✅ {interaction.user.display_name} confirmed step {self.step}')
        session = self.onboarding.active_sessions[user_id]
        session["answers"].update(self.answers)
        await self.onboarding.send_next_question(interaction, step=self.step + 1, answers=session["answers"])


class PersonalizationOptInView(discord.ui.View):
    """View for the personalization opt-in step (synthetic step after guild questions)."""
    def __init__(self, onboarding: Onboarding, answers: dict, n_questions: int):
        super().__init__(timeout=None)
        self.onboarding = onboarding
        self.answers = answers
        self.n_questions = n_questions
        for label, value in onboarding.PERSONALIZATION_OPT_IN_OPTIONS:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=f"personalization_optin_{value}")
            btn.callback = self._make_callback(value)
            self.add_item(btn)

    def _make_callback(self, value: str):
        async def callback(interaction: discord.Interaction):
            user_id = interaction.user.id
            session = self.onboarding.active_sessions.get(user_id)
            if session and session.get("_opt_in_submitted"):
                await interaction.response.defer(ephemeral=True)
                await interaction.followup.send("Onboarding is already complete. Your preferences were saved.", ephemeral=True)
                return
            if session:
                session["_opt_in_submitted"] = True
            self.answers["personalized_opt_in"] = value
            if user_id in self.onboarding.active_sessions:
                self.onboarding.active_sessions[user_id]["answers"]["personalized_opt_in"] = value
            next_step = self.n_questions + 1 if value in ("full", "events_only") else self.n_questions + 2
            await interaction.response.defer(ephemeral=True)
            await self.onboarding.send_next_question(interaction, step=next_step, answers=self.answers)
        return callback


class PersonalizationLanguageView(discord.ui.View):
    """View for the preferred language step (synthetic step; only shown when opt-in is full or events_only)."""
    def __init__(self, onboarding: Onboarding, answers: dict, n_questions: int):
        super().__init__(timeout=None)
        self.onboarding = onboarding
        self.answers = answers
        self.n_questions = n_questions
        opts = [discord.SelectOption(label=label, value=val) for label, val in onboarding.PERSONALIZATION_LANGUAGE_OPTIONS]
        select = discord.ui.Select(
            placeholder="Choose a language...",
            min_values=1,
            max_values=1,
            options=opts,
            custom_id="personalization_language_select"
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        data = interaction.data or {}
        values = data.get("values", []) if isinstance(data, dict) else []
        value = values[0] if values else None
        if not value:
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send("⚠️ Something went wrong. Please try again.", ephemeral=True)
            return
        if value == "other":
            await interaction.response.send_modal(
                OtherLanguageModal(onboarding=self.onboarding, answers=self.answers, n_questions=self.n_questions)
            )
            return
        user_id = interaction.user.id
        session = self.onboarding.active_sessions.get(user_id)
        if session and session.get("_language_submitted"):
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send("Onboarding is already complete. Your preferences were saved.", ephemeral=True)
            return
        if session:
            session["_language_submitted"] = True
        self.answers["preferred_language"] = value
        if user_id in self.onboarding.active_sessions:
            self.onboarding.active_sessions[user_id]["answers"]["preferred_language"] = value
        await interaction.response.defer(ephemeral=True)
        await self.onboarding.send_next_question(interaction, step=self.n_questions + 2, answers=self.answers)


class OtherLanguageModal(discord.ui.Modal):
    """Modal for free-text language when user chooses 'Other language…'."""
    def __init__(self, onboarding: Onboarding, answers: dict, n_questions: int):
        super().__init__(title="Preferred language")
        self.onboarding = onboarding
        self.answers = answers
        self.n_questions = n_questions
        self.input_field = discord.ui.TextInput(
            label="Which language exactly? (type the name or code, e.g. Italiano, Русский, etc.)",
            placeholder="e.g. Italiano, 日本語",
            style=discord.TextStyle.short,
            required=True
        )
        self.add_item(self.input_field)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        session = self.onboarding.active_sessions.get(user_id)
        if session and session.get("_language_submitted"):
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send("Onboarding is already complete. Your preferences were saved.", ephemeral=True)
            return
        if session:
            session["_language_submitted"] = True
        raw = self.input_field.value.strip()
        if not raw:
            self.answers["preferred_language"] = "en"
        else:
            self.answers["preferred_language"] = f"other: {raw}"
        if user_id in self.onboarding.active_sessions:
            self.onboarding.active_sessions[user_id]["answers"]["preferred_language"] = self.answers["preferred_language"]
        await interaction.response.defer(ephemeral=True)
        await self.onboarding.send_next_question(interaction, step=self.n_questions + 2, answers=self.answers)


class FollowupModal(discord.ui.Modal):
    """Modal for follow-up questions where the user can enter text."""
    def __init__(self, title: str, question: str, step: int, answers: dict, validate_email: bool = False, onboarding: Optional['Onboarding'] = None):
        super().__init__(title=title)
        self.step = step
        self.answers = answers
        self.question = question
        self.validate_email = validate_email
        self.onboarding = onboarding
        self.input_field = discord.ui.TextInput(label=question, placeholder="Type your answer here...")
        self.add_item(self.input_field)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        value = self.input_field.value.strip()

        # Email validation if required
        if self.validate_email:
            bot_client = cast(commands.Bot, interaction.client)
            onboarding_cog: Onboarding = self.onboarding or cast(Onboarding, bot_client.get_cog("Onboarding"))
            if not onboarding_cog.EMAIL_REGEX.match(value):
                # Offer a retry button to try again
                await interaction.response.send_message(
                    "❌ Invalid email format. Please try again.",
                    view=ReenterEmailView(step=self.step, answers=self.answers, onboarding=onboarding_cog),
                    ephemeral=True
                )
                return

        # Store follow-up alongside the choice
        existing = self.answers.get(self.step)
        if isinstance(existing, dict):
            existing["followup"] = value
            existing["followup_label"] = self.question
            self.answers[self.step] = existing
        else:
            self.answers[self.step] = {"choice": existing, "followup": value, "followup_label": self.question}

        # Update active session
        bot_client2 = cast(commands.Bot, interaction.client)
        onboarding = self.onboarding or cast(Onboarding, bot_client2.get_cog("Onboarding"))
        if user_id in onboarding.active_sessions:
            onboarding.active_sessions[user_id]["answers"][self.step] = self.answers[self.step]

        await interaction.response.defer(ephemeral=True)
        await onboarding.send_next_question(interaction, step=self.step + 1, answers=self.answers)


class ReenterEmailView(discord.ui.View):
    def __init__(self, step: int, answers: dict, onboarding: Onboarding):
        super().__init__(timeout=60)
        self.step = step
        self.answers = answers
        self.onboarding = onboarding

    @discord.ui.button(label="Re-enter email", style=discord.ButtonStyle.primary)
    async def reenter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            FollowupModal(
                title="Email Address",
                question="Please enter your email address",
                step=self.step,
                answers=self.answers,
                validate_email=True,
                onboarding=self.onboarding,
            )
        )

async def setup(bot: commands.Bot):
    cog = Onboarding(bot)
    await bot.add_cog(cog)
    await cog.setup_database()  # Ensure the database is set up correctly
