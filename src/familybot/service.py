from __future__ import annotations

from datetime import date, timedelta
from difflib import SequenceMatcher
from .models import Action, Task


class TaskService:
    def __init__(self, store, default_area: str):
        self.store = store
        self.default_area = default_area

    async def execute(self, action: Action) -> str:
        if action.action == "create":
            if not action.title:
                return "⚠️ Ich konnte keinen Aufgabentitel erkennen."
            task = Task(
                title=action.title,
                status="Open" if action.due else "Inbox",
                due=action.due,
                priority=action.priority or "Normal",
                reminder=action.reminder or "Normal",
                area=action.area or self.default_area,
                notes=action.notes,
            )
            await self.store.create(task)
            return self.format_confirmation("Neu", task)

        if action.action in {"update", "complete"}:
            match = await self._resolve(action.task_query or action.title or "")
            if isinstance(match, str):
                return match
            changes = {}
            if action.action == "complete":
                changes["status"] = "Done"
            else:
                for field in ("title", "priority", "reminder", "area", "notes"):
                    value = getattr(action, field)
                    if value is not None:
                        changes[field] = value
                if action.clear_due:
                    changes["due"] = None
                elif action.due is not None:
                    changes["due"] = action.due
                if action.due is not None and match.status == "Inbox":
                    changes["status"] = "Open"
            await self.store.update(match.id, changes)
            updated = match.model_copy(update=changes)
            return self.format_confirmation("Erledigt" if action.action == "complete" else "Geändert", updated)

        if action.action == "list":
            tasks = await self.store.open_tasks()
            return self.format_list(self._scope(tasks, action.list_scope or "open"))

        return "Okay."

    async def _resolve(self, query: str):
        if not query.strip():
            return "⚠️ Welche Aufgabe meinst du?"
        exactish = await self.store.find(query.strip())
        if len(exactish) == 1:
            return exactish[0]
        candidates = exactish or await self.store.open_tasks()
        scored = sorted(
            ((SequenceMatcher(None, query.lower(), t.title.lower()).ratio(), t) for t in candidates),
            key=lambda x: x[0], reverse=True,
        )
        if not scored or scored[0][0] < 0.42:
            return f"⚠️ Ich finde keine offene Aufgabe passend zu „{query}“."
        if len(scored) > 1 and scored[1][0] > scored[0][0] - 0.08:
            opts = "\n".join(f"• {x[1].title}" for x in scored[:3])
            return f"⚠️ Nicht eindeutig. Meinst du eine davon?\n{opts}"
        return scored[0][1]

    @staticmethod
    def _scope(tasks: list[Task], scope: str) -> list[Task]:
        today = date.today()
        if scope == "today":
            return [t for t in tasks if t.due == today]
        if scope == "tomorrow":
            return [t for t in tasks if t.due == today + timedelta(days=1)]
        if scope == "overdue":
            return [t for t in tasks if t.due and t.due < today]
        if scope == "inbox":
            return [t for t in tasks if t.status == "Inbox" or t.due is None]
        return tasks

    @staticmethod
    def format_confirmation(prefix: str, t: Task) -> str:
        pri = {"High": "🔴", "Normal": "🟠", "Low": "⚪"}[t.priority]
        due = t.due.strftime("%d.%m.%Y") if t.due else "ohne Termin"
        rem = " · 🔔 dringend" if t.reminder == "Urgent" else ""
        return f"✅ {prefix}: {pri} {t.title}\n📅 {due}{rem}"

    @staticmethod
    def format_list(tasks: list[Task], heading: str | None = None) -> str:
        if not tasks:
            return (heading + "\n" if heading else "") + "🎉 Nichts offen."
        order = {"High": 0, "Normal": 1, "Low": 2}
        tasks = sorted(tasks, key=lambda t: (t.due is None, t.due or date.max, order[t.priority]))
        lines = []
        for t in tasks:
            icon = {"High": "🔴", "Normal": "🟠", "Low": "⚪"}[t.priority]
            due = t.due.strftime("%d.%m.") if t.due else "Inbox"
            urgent = " 🔔" if t.reminder == "Urgent" else ""
            lines.append(f"{icon} {t.title} — {due}{urgent}")
        return (heading + "\n\n" if heading else "") + "\n".join(lines)
