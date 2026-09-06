from __future__ import annotations

from datetime import date
import httpx
from .models import Task


class NotionStore:
    def __init__(self, token: str, data_source_id: str, version: str):
        self.data_source_id = data_source_id
        self.client = httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": version,
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    async def close(self):
        await self.client.aclose()

    @staticmethod
    def _rt(value: str | None):
        return {"rich_text": [{"text": {"content": value}}]} if value else {"rich_text": []}

    def _props(self, t: Task):
        p = {
            "Name": {"title": [{"text": {"content": t.title}}]},
            "Status": {"select": {"name": t.status}},
            "Priority": {"select": {"name": t.priority}},
            "Reminder": {"select": {"name": t.reminder}},
            "Area": {"select": {"name": t.area}},
            "Notes": self._rt(t.notes),
            "Source": {"select": {"name": t.source}},
        }
        p["Due"] = {"date": {"start": t.due.isoformat()}} if t.due else {"date": None}
        return p

    async def create(self, task: Task) -> Task:
        r = await self.client.post("/pages", json={
            "parent": {"type": "data_source_id", "data_source_id": self.data_source_id},
            "properties": self._props(task),
        })
        r.raise_for_status()
        task.id = r.json()["id"]
        return task

    async def update(self, task_id: str, changes: dict) -> None:
        props = {}
        if "title" in changes:
            props["Name"] = {"title": [{"text": {"content": changes["title"]}}]}
        if "status" in changes:
            props["Status"] = {"select": {"name": changes["status"]}}
        if "priority" in changes:
            props["Priority"] = {"select": {"name": changes["priority"]}}
        if "reminder" in changes:
            props["Reminder"] = {"select": {"name": changes["reminder"]}}
        if "area" in changes:
            props["Area"] = {"select": {"name": changes["area"]}}
        if "notes" in changes:
            props["Notes"] = self._rt(changes["notes"])
        if "due" in changes:
            d = changes["due"]
            props["Due"] = {"date": {"start": d.isoformat()}} if d else {"date": None}
        r = await self.client.patch(f"/pages/{task_id}", json={"properties": props})
        r.raise_for_status()

    async def query(self, filter_obj: dict | None = None, sorts: list | None = None) -> list[Task]:
        payload = {"page_size": 100}
        if filter_obj:
            payload["filter"] = filter_obj
        if sorts:
            payload["sorts"] = sorts
        r = await self.client.post(f"/data_sources/{self.data_source_id}/query", json=payload)
        r.raise_for_status()
        return [self._from_page(x) for x in r.json().get("results", [])]

    async def open_tasks(self) -> list[Task]:
        return await self.query({"property": "Status", "select": {"does_not_equal": "Done"}}, [
            {"property": "Due", "direction": "ascending"}
        ])

    async def find(self, text: str) -> list[Task]:
        # Notion title filter is substring based; keep open-only to reduce accidental matches.
        return await self.query({"and": [
            {"property": "Status", "select": {"does_not_equal": "Done"}},
            {"property": "Name", "title": {"contains": text}},
        ]})

    @staticmethod
    def _plain(prop: dict, key: str) -> str | None:
        vals = prop.get(key, {}).get("rich_text", [])
        return "".join(v.get("plain_text", "") for v in vals) or None

    @staticmethod
    def _from_page(page: dict) -> Task:
        p = page["properties"]
        title = "".join(x.get("plain_text", "") for x in p["Name"].get("title", []))
        due_obj = p.get("Due", {}).get("date")
        return Task(
            id=page["id"],
            title=title,
            status=(p.get("Status", {}).get("select") or {}).get("name", "Open"),
            due=date.fromisoformat(due_obj["start"][:10]) if due_obj else None,
            priority=(p.get("Priority", {}).get("select") or {}).get("name", "Normal"),
            reminder=(p.get("Reminder", {}).get("select") or {}).get("name", "Normal"),
            area=(p.get("Area", {}).get("select") or {}).get("name", "Sonstiges"),
            notes=NotionStore._plain(p, "Notes"),
            source=(p.get("Source", {}).get("select") or {}).get("name", "Telegram"),
        )
