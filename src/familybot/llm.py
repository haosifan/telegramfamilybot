from __future__ import annotations

from datetime import datetime
import json
import httpx
from .models import ActionPlan

SYSTEM = """Du bist der Parser eines persönlichen Aufgabenassistenten.
Der Benutzer spricht Deutsch und kann mehrere Änderungen in einem Satz nennen.

Erlaubte Aktionen:
- create: neue Aufgabe
- update: bestehende Aufgabe ändern
- complete: bestehende Aufgabe erledigen
- list: Aufgaben abfragen
- noop: keine Task-Aktion

Regeln:
- Erfinde kein Fälligkeitsdatum, wenn keines genannt oder eindeutig impliziert ist.
- Bei 'morgen', Wochentagen etc. löse auf ein konkretes ISO-Datum auf Basis des mitgegebenen aktuellen Datums auf.
- 'dringend' als Erinnerung => reminder=Urgent; wenn zugleich Wichtigkeit gemeint ist, priority=High.
- task_query soll bei update/complete eine kurze charakteristische Teilzeichenfolge des Aufgabentitels sein.
- Wenn eine Referenz zu unklar ist, setze clarification statt zu raten.
- Für create ohne Datum: due=null. Der Status Inbox/Open wird außerhalb des Parsers bestimmt.
- Gib ausschließlich Daten zurück, die dem vorgegebenen JSON-Schema entsprechen.
"""

ACTION_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "enum": ["create", "update", "complete", "list", "noop"]},
                    "task_query": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                    "due": {"type": ["string", "null"]},
                    "clear_due": {"type": "boolean"},
                    "priority": {"type": ["string", "null"], "enum": ["Low", "Normal", "High", None]},
                    "reminder": {"type": ["string", "null"], "enum": ["None", "Normal", "Urgent", None]},
                    "area": {"type": ["string", "null"]},
                    "notes": {"type": ["string", "null"]},
                    "list_scope": {"type": ["string", "null"], "enum": ["today", "tomorrow", "open", "overdue", "inbox", None]},
                },
                "required": [
                    "action", "task_query", "title", "due", "clear_due", "priority",
                    "reminder", "area", "notes", "list_scope"
                ],
            },
        },
        "clarification": {"type": ["string", "null"]},
    },
    "required": ["actions", "clarification"],
}


class LLMParser:
    """Task parser using the OpenAI Responses API with Structured Outputs."""

    def __init__(self, api_key: str, model: str, timezone: str):
        self.model = model
        self.timezone = timezone
        self.client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=90,
        )

    async def close(self):
        await self.client.aclose()

    @staticmethod
    def _extract_output_text(payload: dict) -> str:
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return content["text"]
        raise ValueError("OpenAI response contained no output_text")

    async def parse(self, text: str, now: datetime) -> ActionPlan:
        user = f"Aktuelles Datum/Zeit ({self.timezone}): {now.isoformat()}\nBenutzereingabe: {text}"
        response = await self.client.post(
            "/responses",
            json={
                "model": self.model,
                "store": False,
                "reasoning": {"effort": "none"},
                "instructions": SYSTEM,
                "input": user,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "family_task_action_plan",
                        "schema": ACTION_PLAN_SCHEMA,
                        "strict": True,
                    }
                },
            },
        )
        response.raise_for_status()
        content = self._extract_output_text(response.json())
        return ActionPlan.model_validate(json.loads(content))
