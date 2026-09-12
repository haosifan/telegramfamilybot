from __future__ import annotations

from datetime import datetime
import json

import httpx

from .models import ActionPlan

SYSTEM = """Du bist der Parser eines persönlichen Aufgabenassistenten.
Der Benutzer spricht Deutsch und kann mehrere Änderungen in einem Satz nennen.

Erlaubte Aktionen:
- create_task: neue Aufgabe
- update_task: Eigenschaften einer bestehenden Aufgabe ändern (Termin, Priorität, Notizen usw.)
- rename_task: ausschließlich den Titel einer bestehenden Aufgabe ändern
- complete_task: bestehende Aufgabe als erledigt markieren
- delete_task: bestehende Aufgabe in den Notion-Papierkorb verschieben
- list_tasks: Aufgaben abfragen
- select_task: eine nummerierte Aufgabe aus dem kurzlebigen Dialogkontext auswählen
- noop: keine Task-Aktion

Strikte Regeln:
- Completion und Änderungen bestehender Tasks haben Vorrang vor Create. Ein Satz wie "Paperless sortieren. Done."
  ist complete_task mit task_query="Paperless sortieren", niemals create_task.
- Entscheide, worauf sich "löschen" oder "entfernen" bezieht: "Task/Aufgabe löschen" ist delete_task.
  "Deadline/Termin/Fälligkeitsdatum löschen", "ohne Termin" und "kein Termin" sind update_task
  mit clear_fields=["due"]. Suche dabei niemals eine Aufgabe namens "Deadline" oder "Termin".
- "Reminder/Erinnerung löschen" und "nicht mehr dringend" sind update_task mit
  clear_fields=["reminder"]. Ein null-Wert bedeutet bei Update immer "Feld nicht ändern";
  nur clear_fields entfernt einen vorhandenen Wert.
- Wenn eine Eingabe einen Task bezeichnet und zusätzlich Termin, Priorität, Reminder oder Notizen
  verändert, verwende update_task statt create_task. Beispiele: "Telefon besorgen, morgen",
  "Telefon besorgen, 18.09.", "Telefon besorgen, Priorität hoch".
- "erledigt", "done", "abschließen" und "abhaken" bedeuten complete_task.
- Für eine reine Titeländerung verwende rename_task: task_query bezeichnet den bisherigen Titel,
  title enthält den neuen vollständigen Titel.
- task_query ist bei update_task/rename_task/complete_task/delete_task eine kurze charakteristische Teilzeichenfolge.
- Referenzen auf die zuletzt betroffene Aufgabe erhalten reference="last_task". Nummerierte Referenzen
  erhalten select_task und selection_index (1-basiert). Erfinde niemals eine ID; IDs sind kein Ausgabefeld.
- Nutze den Dialogkontext nur, wenn Eingabe und Kontext plausibel zusammengehören. Frage sonst nach.
- Erfinde kein Fälligkeitsdatum, wenn keines genannt oder eindeutig impliziert ist.
- Bei "morgen", Wochentagen etc. löse ein ISO-Datum anhand des aktuellen Datums auf.
- "dringend" als Erinnerung => reminder=Urgent; wenn zugleich Wichtigkeit gemeint ist, priority=High.
- Für create_task ohne Datum: due=null. Inbox/Open wird außerhalb des Parsers bestimmt.
- clear_fields darf nur bei update_task befüllt werden und enthält ausschließlich wirklich zu
  entfernende Felder. Ansonsten ist es eine leere Liste.
- Gib ausschließlich Daten zurück, die dem vorgegebenen JSON-Schema entsprechen.
"""

ACTION_NAMES = [
    "create_task", "update_task", "rename_task", "complete_task", "delete_task",
    "list_tasks", "select_task", "noop",
]

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
                    "action": {"type": "string", "enum": ACTION_NAMES},
                    "task_query": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                    "due": {"type": ["string", "null"]},
                    "clear_fields": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["due", "reminder", "notes"]},
                    },
                    "priority": {"type": ["string", "null"], "enum": ["Low", "Normal", "High", None]},
                    "reminder": {"type": ["string", "null"], "enum": ["None", "Normal", "Urgent", None]},
                    "area": {"type": ["string", "null"]},
                    "notes": {"type": ["string", "null"]},
                    "list_scope": {
                        "type": ["string", "null"],
                        "enum": ["today", "tomorrow", "open", "overdue", "inbox", None],
                    },
                    "selection_index": {"type": ["integer", "null"], "minimum": 1},
                    "reference": {
                        "type": ["string", "null"],
                        "enum": ["last_task", "presented_task", None],
                    },
                },
                "required": [
                    "action", "task_query", "title", "due", "clear_fields", "priority",
                    "reminder", "area", "notes", "list_scope", "selection_index", "reference",
                ],
            },
        },
        "clarification": {"type": ["string", "null"]},
    },
    "required": ["actions", "clarification"],
}


class LLMParser:
    """Task parser using the OpenAI Responses API with strict Structured Outputs."""

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

    async def parse(
        self,
        text: str,
        now: datetime,
        conversation_context: dict | None = None,
    ) -> ActionPlan:
        user = (
            f"Aktuelles Datum/Zeit ({self.timezone}): {now.isoformat()}\n"
            f"Kurzlebiger Dialogkontext (kann fehlen): "
            f"{json.dumps(conversation_context, ensure_ascii=False)}\n"
            f"Benutzereingabe: {text}"
        )
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
