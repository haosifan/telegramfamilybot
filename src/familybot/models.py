from datetime import date
from typing import Literal
from pydantic import BaseModel, Field

Priority = Literal["Low", "Normal", "High"]
Reminder = Literal["None", "Normal", "Urgent"]
Status = Literal["Inbox", "Open", "Done"]


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


class Action(BaseModel):
    action: Literal["create", "update", "complete", "list", "noop"]
    task_query: str | None = None
    title: str | None = None
    due: date | None = None
    clear_due: bool = False
    priority: Priority | None = None
    reminder: Reminder | None = None
    area: str | None = None
    notes: str | None = None
    list_scope: Literal["today", "tomorrow", "open", "overdue", "inbox"] | None = None


class ActionPlan(BaseModel):
    actions: list[Action] = Field(default_factory=list)
    clarification: str | None = None
