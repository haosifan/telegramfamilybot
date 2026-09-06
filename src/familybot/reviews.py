from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .service import TaskService


class Reviews:
    def __init__(self, service: TaskService, tz: str):
        self.service = service
        self.tz = ZoneInfo(tz)
        self._last_urgent_sent: dict[str, str] = {}

    async def morning_text(self) -> str:
        tasks = await self.service.store.open_tasks()
        today = datetime.now(self.tz).date()
        overdue = [t for t in tasks if t.due and t.due < today]
        due = [t for t in tasks if t.due == today]
        inbox = [t for t in tasks if t.due is None]
        parts = ["☀️ Guten Morgen — dein Tagescheck"]
        parts.append(self.service.format_list(due, "Heute"))
        if overdue:
            parts.append(self.service.format_list(overdue, "Überfällig"))
        if inbox:
            parts.append(f"📥 {len(inbox)} Aufgabe(n) warten noch ohne Termin in der Inbox.")
        parts.append("\nAntworte einfach, z. B.: „Paket morgen dringend, Rechnung Freitag, Schule erledigt.“")
        return "\n\n".join(parts)

    async def evening_text(self) -> str:
        tasks = await self.service.store.open_tasks()
        today = datetime.now(self.tz).date()
        open_today = [t for t in tasks if t.due and t.due <= today]
        text = self.service.format_list(open_today, "🌙 Abendschleife — noch offen")
        return text + "\n\nDu kannst mehrere Änderungen in einer Nachricht diktieren."

    async def urgent_text(self) -> str | None:
        now = datetime.now(self.tz)
        tasks = await self.service.store.open_tasks()
        urgent = [t for t in tasks if t.due == now.date() and t.reminder == "Urgent"]
        if not urgent:
            return None
        key = now.strftime("%Y-%m-%d-%H")
        unsent = [t for t in urgent if self._last_urgent_sent.get(t.id or t.title) != key]
        if not unsent:
            return None
        for t in unsent:
            self._last_urgent_sent[t.id or t.title] = key
        return self.service.format_list(unsent, "🔔 Dringende Erinnerung")
