"""Tests for legacy reminder repository helpers in cogs.reminders."""

import asyncio
import unittest
from typing import Any

from cogs.reminders import create_reminder, delete_reminder, get_reminders_for_user, update_reminder


class FakeConn:
    def __init__(self):
        self.rows: list[dict[str, Any]] = []
        self.deleted: list[int] = []

    async def execute(self, query: str, *params):
        if query.strip().lower().startswith("insert into reminders"):
            name, channel_id, time_obj, days, message, created_by = params
            new_id = len(self.rows) + 1
            self.rows.append({
                "id": new_id,
                "name": name,
                "channel_id": channel_id,
                "time": time_obj,
                "days": days,
                "message": message,
                "created_by": created_by
            })
        elif query.strip().lower().startswith("update reminders"):
            name, time_obj, days, message, rid, created_by = params
            for r in self.rows:
                if r["id"] == rid and r["created_by"] == created_by:
                    r.update({"name": name, "time": time_obj, "days": days, "message": message})
        elif query.strip().lower().startswith("delete from reminders"):
            rid, created_by = params
            self.rows = [r for r in self.rows if not (r["id"] == rid and r["created_by"] == created_by)]
            self.deleted.append(rid)

    async def fetch(self, query: str, *params):
        (uid,) = params
        return [r for r in self.rows if r["created_by"] == uid]


class TestRemindersRepo(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConn()

    def test_create_and_get(self):
        async def scenario():
            await create_reminder(self.conn, {
                "name": "Test",
                "channel_id": "123",
                "time": "08:00",
                "days": ["1"],
                "message": "Hi",
                "created_by": "u1"
            })
            rows = await get_reminders_for_user(self.conn, "u1")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], "Test")
        asyncio.run(scenario())

    def test_update(self):
        async def scenario():
            await create_reminder(self.conn, {
                "name": "Old",
                "channel_id": "123",
                "time": "08:00",
                "days": ["1"],
                "message": "Hi",
                "created_by": "u1"
            })
            await update_reminder(self.conn, {
                "id": 1,
                "name": "New",
                "time": "09:00",
                "days": ["2"],
                "message": "Yo",
                "created_by": "u1"
            })
            rows = await get_reminders_for_user(self.conn, "u1")
            self.assertEqual(rows[0]["name"], "New")
            self.assertEqual(rows[0]["time"], "09:00")
        asyncio.run(scenario())

    def test_delete(self):
        async def scenario():
            await create_reminder(self.conn, {
                "name": "Old",
                "channel_id": "123",
                "time": "08:00",
                "days": ["1"],
                "message": "Hi",
                "created_by": "u1"
            })
            await delete_reminder(self.conn, 1, "u1")
            rows = await get_reminders_for_user(self.conn, "u1")
            self.assertEqual(len(rows), 0)
        asyncio.run(scenario())
