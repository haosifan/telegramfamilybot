from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
import logging
import re
import unicodedata

from .models import Action, ActionPlan, ConversationState, Task, TaskReference

log = logging.getLogger(__name__)


@dataclass
class Resolution:
    task: Task | None = None
    candidates: list[Task] | None = None
    error: str | None = None


class TaskService:
    def __init__(self, store, default_area: str, state_store=None):
        self.store = store
        self.default_area = default_area
        self.state_store = state_store

    async def handle_message(self, chat_id: int, text: str, parser, now: datetime) -> str:
        """Parse and execute one message with short-lived per-chat context."""
        state = self.state_store.get(chat_id, now) if self.state_store else None

        selection = self._selection_index(text)
        if selection is not None and state and state.pending_intent:
            if selection >= len(state.pending_candidates):
                return f"⚠️ Bitte wähle eine Nummer zwischen 1 und {len(state.pending_candidates)}."
            action = Action.model_validate(state.pending_intent)
            chosen = state.pending_candidates[selection]
            action.task_id = chosen.id
            log.info("resolved intent=%s state_used=true selected_task_id=%s", action.action, chosen.id)
            return await self.execute(action, chat_id=chat_id, now=now, state=state)
        if selection is not None and not state:
            return "⚠️ Die frühere Auswahl ist nicht mehr verfügbar. Welche Aufgabe meinst du?"

        correction = self._correction_action(text, state)
        if correction:
            log.info("resolved intent=%s state_used=true correction_applied=true", correction.action)
            return await self.execute(correction, chat_id=chat_id, now=now, state=state)

        context = state.prompt_context() if state else None
        plan = await parser.parse(text, now, conversation_context=context)
        if plan.clarification:
            return f"❓ {plan.clarification}"
        if not plan.actions:
            return "Ich habe darin keine Aufgabenaktion erkannt."

        actions = self._apply_deterministic_intent_priority(text, plan)
        results = []
        for action in actions:
            current = self.state_store.get(chat_id, now) if self.state_store else state
            action = self._resolve_state_reference(action, text, current)
            log.info(
                "resolved intent=%s state_used=%s selected_task_id=%s",
                action.action,
                bool(action.task_id),
                action.task_id,
            )
            results.append(await self.execute(action, chat_id=chat_id, now=now, state=current))
        return "\n\n".join(results)

    async def execute(
        self,
        action: Action,
        chat_id: int | None = None,
        now: datetime | None = None,
        state: ConversationState | None = None,
    ) -> str:
        if action.action == "create_task":
            if not action.title:
                return "⚠️ Ich konnte keinen Aufgabentitel erkennen."
            duplicate = await self._possible_duplicate(action.title)
            if duplicate:
                return (
                    f"⚠️ Eine sehr ähnliche offene Aufgabe existiert bereits: „{duplicate.title}“. "
                    "Bitte gib einen abweichenden Titel an, falls du bewusst eine zweite Aufgabe möchtest."
                )
            task = Task(
                title=action.title,
                status="Open" if action.due else "Inbox",
                due=action.due,
                priority=action.priority or "Normal",
                reminder=action.reminder or "Normal",
                area=action.area or self.default_area,
                notes=action.notes,
            )
            task = await self.store.create(task)
            self._remember(chat_id, now, state, action.action, task)
            return self.format_confirmation("Neu", task)

        if action.action in {"update_task", "rename_task", "complete_task", "delete_task"}:
            resolution = await self._resolve(action.task_query or action.title or "", action.task_id)
            if resolution.error:
                return resolution.error
            if resolution.candidates:
                self._remember_ambiguity(chat_id, now, state, action, resolution.candidates)
                log.info("ambiguity_detected=true intent=%s candidates=%d", action.action, len(resolution.candidates))
                options = "\n".join(f"{i}. {task.title}" for i, task in enumerate(resolution.candidates, 1))
                return f"⚠️ Nicht eindeutig. Meinst du eine davon?\n{options}"

            task = resolution.task
            assert task is not None
            if action.action == "delete_task":
                await self.store.delete(task.id)
                self._remember(chat_id, now, state, action.action, task)
                return f"🗑️ Gelöscht: {task.title}"

            changes = {}
            if action.action == "complete_task":
                changes["status"] = "Done"
            elif action.action == "rename_task":
                if not action.title:
                    return "⚠️ Wie soll der neue vollständige Titel lauten?"
                changes["title"] = action.title
            else:
                for field in ("title", "priority", "reminder", "area", "notes"):
                    value = getattr(action, field)
                    if value is not None:
                        changes[field] = value
                if action.clear_due:
                    changes["due"] = None
                elif action.due is not None:
                    changes["due"] = action.due
                if action.due is not None and task.status == "Inbox":
                    changes["status"] = "Open"
            if not changes:
                return "⚠️ Ich konnte keine gewünschte Änderung erkennen."
            await self.store.update(task.id, changes)
            updated = task.model_copy(update=changes)
            self._remember(chat_id, now, state, action.action, updated)
            prefix = "Erledigt" if action.action == "complete_task" else "Geändert"
            return self.format_confirmation(prefix, updated)

        if action.action == "list_tasks":
            tasks = self._scope(await self.store.open_tasks(), action.list_scope or "open")
            self._remember_list(chat_id, now, state, tasks)
            return self.format_list(tasks)

        if action.action == "select_task":
            if not action.task_id:
                return "⚠️ Welche der zuvor genannten Aufgaben meinst du?"
            task = await self._get_by_id(action.task_id)
            if not task:
                return "⚠️ Die zuvor referenzierte Aufgabe ist nicht mehr verfügbar."
            self._remember(chat_id, now, state, action.action, task)
            return f"✅ Ausgewählt: {task.title}"
        return "Okay."

    async def _get_by_id(self, task_id: str) -> Task | None:
        if hasattr(self.store, "get"):
            return await self.store.get(task_id)
        return next((task for task in await self.store.open_tasks() if task.id == task_id), None)

    async def _resolve(self, query: str, task_id: str | None = None) -> Resolution:
        if task_id:
            task = await self._get_by_id(task_id)
            if task:
                return Resolution(task=task)
            return Resolution(error="⚠️ Die zuvor referenzierte Aufgabe ist nicht mehr verfügbar.")
        if not query.strip():
            return Resolution(error="⚠️ Welche Aufgabe meinst du?")
        exactish = await self.store.find(query.strip())
        if len(exactish) == 1:
            return Resolution(task=exactish[0])
        candidates = exactish or await self.store.open_tasks()
        scored = sorted(
            ((self._similarity(query, task.title), task) for task in candidates),
            key=lambda item: item[0],
            reverse=True,
        )
        if not scored or scored[0][0] < 0.42:
            return Resolution(error=f"⚠️ Ich finde keine offene Aufgabe passend zu „{query}“.")
        if len(scored) > 1 and scored[1][0] > scored[0][0] - 0.08:
            return Resolution(candidates=[item[1] for item in scored[:3]])
        return Resolution(task=scored[0][1])

    async def _possible_duplicate(self, title: str) -> Task | None:
        matches = await self.store.find(title.strip())
        candidates = matches or await self.store.open_tasks()
        similar = [task for task in candidates if self._similarity(title, task.title) >= 0.94]
        return similar[0] if similar else None

    def _remember(
        self,
        chat_id: int | None,
        now: datetime | None,
        state: ConversationState | None,
        action_name: str,
        task: Task,
    ) -> None:
        if not self.state_store or chat_id is None or not task.id:
            return
        state = state or self.state_store.new(chat_id, now)
        state.last_action = action_name
        state.last_task_id = task.id
        state.last_task_title = task.title
        state.pending_intent = None
        state.pending_candidates = []
        self.state_store.save(state, now)

    def _remember_ambiguity(
        self,
        chat_id: int | None,
        now: datetime | None,
        state: ConversationState | None,
        action: Action,
        candidates: list[Task],
    ) -> None:
        if not self.state_store or chat_id is None:
            return
        state = state or self.state_store.new(chat_id, now)
        state.pending_intent = action.model_copy(update={"task_id": None}).model_dump(mode="json")
        state.pending_candidates = [TaskReference(id=t.id, title=t.title) for t in candidates if t.id]
        self.state_store.save(state, now)

    def _remember_list(
        self,
        chat_id: int | None,
        now: datetime | None,
        state: ConversationState | None,
        tasks: list[Task],
    ) -> None:
        if not self.state_store or chat_id is None:
            return
        state = state or self.state_store.new(chat_id, now)
        state.last_action = "list_tasks"
        state.last_presented_tasks = [TaskReference(id=t.id, title=t.title) for t in tasks if t.id]
        state.pending_intent = None
        state.pending_candidates = []
        self.state_store.save(state, now)

    @classmethod
    def _similarity(cls, left: str, right: str) -> float:
        return SequenceMatcher(None, cls._normalized(left), cls._normalized(right)).ratio()

    @staticmethod
    def _normalized(value: str) -> str:
        value = unicodedata.normalize("NFKC", value).casefold()
        return " ".join(re.findall(r"\w+", value))

    @classmethod
    def _selection_index(cls, text: str) -> int | None:
        value = cls._normalized(text)
        value = re.sub(r"^(davon )?(das |die |den )?", "", value)
        words = {
            "erste": 0, "ersten": 0, "erstes": 0, "erster": 0,
            "zweite": 1, "zweiten": 1, "zweites": 1, "zweiter": 1,
            "dritte": 2, "dritten": 2, "drittes": 2, "dritter": 2,
        }
        if value in words:
            return words[value]
        match = re.fullmatch(r"(?:nummer )?(\d+)", value)
        return int(match.group(1)) - 1 if match and int(match.group(1)) > 0 else None

    @classmethod
    def _correction_action(cls, text: str, state: ConversationState | None) -> Action | None:
        if not state or not state.last_task_id or state.last_action not in {
            "create_task", "update_task", "rename_task"
        }:
            return None
        normalized = cls._normalized(text)
        correction_starts = (
            "nein ich meinte", "der komplette titel ist", "mach daraus", "korrigiere das zu",
            "ändere das nochmal in", "aendere das nochmal in", "eigentlich",
        )
        if not normalized.startswith(correction_starts):
            return None
        quoted = re.findall(r"[\"'„“‚‘]([^\"'„“‚‘]+)[\"'„“‚‘]", text)
        if quoted:
            title = quoted[-1].strip()
        else:
            title = re.sub(
                r"^(?:Nein,? ich meinte|Der komplette Titel ist|Mach daraus|Korrigiere das zu|"
                r"Ändere das nochmal in|Eigentlich)\s*[:,-]?\s*",
                "",
                text,
                flags=re.IGNORECASE,
            ).strip(" .")
        if not title:
            return None
        return Action(action="rename_task", task_id=state.last_task_id, title=title)

    @classmethod
    def _apply_deterministic_intent_priority(cls, text: str, plan: ActionPlan) -> list[Action]:
        actions = plan.actions
        if len(actions) != 1:
            return actions
        action = actions[0]
        normalized = cls._normalized(text)
        query = action.task_query or action.title or text
        if re.search(r"\b(löschen|loeschen|entfernen)\b", normalized):
            return [action.model_copy(update={"action": "delete_task", "task_query": cls._clean_query(query)})]
        if re.search(r"\b(erledigen|erledigt|done|abschließen|abschliessen|abhaken)\b", normalized):
            return [action.model_copy(update={"action": "complete_task", "task_query": cls._clean_query(query)})]
        rename = re.match(r"^(.+?)\s+in\s+[\"'„“]([^\"'„“]+)[\"'„“]\s+ändern\.?$", text, re.IGNORECASE)
        if rename:
            return [Action(action="rename_task", task_query=rename.group(1).strip(), title=rename.group(2).strip())]
        return actions

    @classmethod
    def _clean_query(cls, value: str) -> str:
        value = re.sub(r"\b(?:die\s+)?aufgabe\b", "", value, flags=re.IGNORECASE)
        value = re.sub(
            r"\b(?:löschen|loeschen|entfernen|erledigen|erledigt|done|abschließen|abschliessen|abhaken)\b",
            "",
            value,
            flags=re.IGNORECASE,
        )
        return value.strip(" .,'\"„“")

    @classmethod
    def _resolve_state_reference(
        cls, action: Action, text: str, state: ConversationState | None
    ) -> Action:
        if action.task_id or not state:
            return action
        normalized = cls._normalized(text)
        last_refs = (
            "die letzte aufgabe", "den letzten", "die letzte", "diese aufgabe",
            "die gerade eben", "das eben erstellte",
        )
        if (action.reference == "last_task" or any(x in normalized for x in last_refs)) and state.last_task_id:
            return action.model_copy(update={"task_id": state.last_task_id})
        if action.selection_index and state.last_presented_tasks:
            index = action.selection_index - 1
            if 0 <= index < len(state.last_presented_tasks):
                return action.model_copy(update={"task_id": state.last_presented_tasks[index].id})
        return action

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
    def format_confirmation(prefix: str, task: Task) -> str:
        priority = {"High": "🔴", "Normal": "🟠", "Low": "⚪"}[task.priority]
        due = task.due.strftime("%d.%m.%Y") if task.due else "ohne Termin"
        reminder = " · 🔔 dringend" if task.reminder == "Urgent" else ""
        return f"✅ {prefix}: {priority} {task.title}\n📅 {due}{reminder}"

    @staticmethod
    def format_list(tasks: list[Task], heading: str | None = None) -> str:
        if not tasks:
            return (heading + "\n" if heading else "") + "🎉 Nichts offen."
        order = {"High": 0, "Normal": 1, "Low": 2}
        tasks = sorted(tasks, key=lambda t: (t.due is None, t.due or date.max, order[t.priority]))
        lines = []
        for task in tasks:
            icon = {"High": "🔴", "Normal": "🟠", "Low": "⚪"}[task.priority]
            due = task.due.strftime("%d.%m.") if task.due else "Inbox"
            urgent = " 🔔" if task.reminder == "Urgent" else ""
            lines.append(f"{icon} {task.title} — {due}{urgent}")
        return (heading + "\n\n" if heading else "") + "\n".join(lines)
