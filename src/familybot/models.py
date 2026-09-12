from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Priority = Literal["Low", "Normal", "High"]
Reminder = Literal["None", "Normal", "Urgent"]
Status = Literal["Inbox", "Open", "Done"]
ClearableField = Literal["due", "reminder", "notes"]
ActionName = Literal[
    "create_task",
    "update_task",
    "rename_task",
    "complete_task",
    "delete_task",
    "list_tasks",
    "select_task",
    "noop",
]


class Task(BaseModel):
    id: str | None = None
    title: str
    status: Status = "Open"
    due: date | None = None
    priority: Priority = "Normal"
    reminder: Reminder = "Normal"
    area: str = "Sonstiges"
    notes: str | None = None
    source: str = "Telegram"


class TaskReference(BaseModel):
    id: str
    title: str


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ActionName
    task_query: str | None = None
    title: str | None = None
    due: date | None = None
    clear_fields: list[ClearableField] = Field(default_factory=list)
    # Backward compatibility for pending intents written by older versions.
    clear_due: bool = False
    priority: Priority | None = None
    reminder: Reminder | None = None
    area: str | None = None
    notes: str | None = None
    list_scope: Literal["today", "tomorrow", "open", "overdue", "inbox"] | None = None
    selection_index: int | None = Field(default=None, ge=1)
    reference: Literal["last_task", "presented_task"] | None = None
    # Internal only. The LLM schema deliberately cannot return Notion IDs.
    task_id: str | None = Field(default=None, exclude=True)


class ActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[Action] = Field(default_factory=list)
    clarification: str | None = None


class ConversationState(BaseModel):
    telegram_chat_id: int
    timestamp: datetime
    expires_at: datetime
    last_action: ActionName | None = None
    last_task_id: str | None = None
    last_task_title: str | None = None
    pending_intent: dict[str, Any] | None = None
    pending_candidates: list[TaskReference] = Field(default_factory=list)
    last_presented_tasks: list[TaskReference] = Field(default_factory=list)

    def prompt_context(self) -> dict[str, Any]:
        """Return useful context without exposing writable task IDs to the LLM."""
        return {
            "last_action": self.last_action,
            "last_task_title": self.last_task_title,
            "pending_candidate_titles": [x.title for x in self.pending_candidates],
            "last_presented_task_titles": [x.title for x in self.last_presented_tasks],
        }
