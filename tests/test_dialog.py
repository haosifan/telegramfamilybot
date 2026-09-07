import asyncio
from datetime import datetime, timedelta, timezone

from familybot.models import Action, ActionPlan, Task, TaskReference
from familybot.service import TaskService
from familybot.state import ConversationStateStore


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, tasks=None):
        self.tasks = {task.id: task for task in (tasks or [])}
        self.created = []
        self.updated = []
        self.deleted = []

    async def create(self, task):
        task.id = task.id or f"new-{len(self.tasks) + 1}"
        self.tasks[task.id] = task
        self.created.append(task.id)
        return task

    async def update(self, task_id, changes):
        self.updated.append((task_id, changes))
        self.tasks[task_id] = self.tasks[task_id].model_copy(update=changes)

    async def delete(self, task_id):
        self.deleted.append(task_id)

    async def get(self, task_id):
        return self.tasks.get(task_id)

    async def open_tasks(self):
        return [task for task in self.tasks.values() if task.status != "Done" and task.id not in self.deleted]

    async def find(self, text):
        query = text.casefold()
        return [task for task in await self.open_tasks() if query in task.title.casefold()]


class StubParser:
    def __init__(self, *plans):
        self.plans = list(plans)
        self.calls = []

    async def parse(self, text, now, conversation_context=None):
        self.calls.append((text, conversation_context))
        return self.plans.pop(0)


def run(coro):
    return asyncio.run(coro)


def service(tmp_path, tasks=None, ttl=20):
    states = ConversationStateStore(tmp_path / "conversation.db", ttl_minutes=ttl)
    store = FakeStore(tasks)
    return TaskService(store, "Sonstiges", states), store, states


def test_a_create_task(tmp_path):
    svc, store, _ = service(tmp_path)
    parser = StubParser(ActionPlan(actions=[Action(action="create_task", title="Paperless sortieren")]))

    result = run(svc.handle_message(1, "Paperless sortieren", parser, NOW))

    assert "Neu" in result
    assert len(store.created) == 1


def test_b_done_takes_priority_over_create_and_does_not_duplicate(tmp_path):
    existing = Task(id="page-a", title="Paperless sortieren")
    svc, store, _ = service(tmp_path, [existing])
    parser = StubParser(ActionPlan(actions=[Action(action="create_task", title="Paperless sortieren")]))

    result = run(svc.handle_message(1, "Paperless sortieren. Done.", parser, NOW))

    assert "Erledigt" in result
    assert store.created == []
    assert store.updated == [("page-a", {"status": "Done"})]


def test_c_and_d_ambiguity_persists_ids_and_selection_uses_first_id(tmp_path):
    tasks = [Task(id="page-a", title="Paperless sortieren"), Task(id="page-b", title="Paperless sortieren")]
    svc, store, states = service(tmp_path, tasks)
    parser = StubParser(ActionPlan(actions=[Action(action="complete_task", task_query="Paperless sortieren")]))

    question = run(svc.handle_message(7, "Paperless sortieren erledigen", parser, NOW))
    saved = states.get(7, NOW)

    assert "1. Paperless sortieren" in question and "2. Paperless sortieren" in question
    assert saved.pending_intent["action"] == "complete_task"
    assert [candidate.id for candidate in saved.pending_candidates] == ["page-a", "page-b"]

    answer = run(svc.handle_message(7, "das erste", parser, NOW + timedelta(seconds=5)))
    assert "Erledigt" in answer
    assert store.updated == [("page-a", {"status": "Done"})]
    assert len(parser.calls) == 1


def test_e_and_f_rename_then_correction_reuses_same_id(tmp_path):
    task = Task(id="page-r", title="Serani nach Kochbuch gucken")
    svc, store, _ = service(tmp_path, [task])
    parser = StubParser(ActionPlan(actions=[Action(action="create_task", title="ignored")]))

    first = run(svc.handle_message(2, "Serani in 'für Rani' ändern", parser, NOW))
    second = run(svc.handle_message(
        2,
        "Der komplette Titel ist 'für Rani nach Kochbuch gucken'",
        parser,
        NOW + timedelta(seconds=10),
    ))

    assert "Geändert" in first and "Geändert" in second
    assert store.updated == [
        ("page-r", {"title": "für Rani"}),
        ("page-r", {"title": "für Rani nach Kochbuch gucken"}),
    ]
    assert len(parser.calls) == 1


def test_g_delete_is_never_complete(tmp_path):
    task = Task(id="page-r", title="für Rani")
    svc, store, _ = service(tmp_path, [task])
    parser = StubParser(ActionPlan(actions=[Action(action="complete_task", task_query="für Rani")]))

    result = run(svc.handle_message(3, "Die Aufgabe 'für Rani' löschen", parser, NOW))

    assert "Gelöscht" in result
    assert store.deleted == ["page-r"]
    assert store.updated == []


def test_h_last_task_reference_uses_state_id(tmp_path):
    task = Task(id="page-last", title="Paket wegbringen")
    svc, store, states = service(tmp_path, [task])
    state = states.new(4, NOW)
    state.last_action = "create_task"
    state.last_task_id = task.id
    state.last_task_title = task.title
    states.save(state, NOW)
    parser = StubParser(ActionPlan(actions=[Action(action="complete_task", reference="last_task")]))

    result = run(svc.handle_message(4, "die letzte Aufgabe erledigen", parser, NOW + timedelta(seconds=2)))

    assert "Erledigt" in result
    assert store.updated == [("page-last", {"status": "Done"})]
    assert parser.calls[0][1]["last_task_title"] == "Paket wegbringen"
    assert "page-last" not in str(parser.calls[0][1])


def test_duplicate_create_is_stopped_without_changing_existing_task(tmp_path):
    task = Task(id="page-existing", title="Paperless sortieren")
    svc, store, _ = service(tmp_path, [task])
    parser = StubParser(ActionPlan(actions=[Action(action="create_task", title="Paperless sortieren")]))

    result = run(svc.handle_message(8, "Paperless sortieren", parser, NOW))

    assert "existiert bereits" in result
    assert store.created == [] and store.updated == []


def test_i_expired_state_does_not_resolve_old_selection(tmp_path):
    svc, _, states = service(tmp_path, ttl=15)
    state = states.new(5, NOW)
    state.pending_intent = Action(action="complete_task", task_query="x").model_dump(mode="json")
    state.pending_candidates = [TaskReference(id="old", title="Alt")]
    states.save(state, NOW)
    parser = StubParser()

    result = run(svc.handle_message(5, "das erste", parser, NOW + timedelta(minutes=16)))

    assert "nicht mehr verfügbar" in result
    assert states.get(5, NOW + timedelta(minutes=16)) is None


def test_j_state_survives_store_reinitialization(tmp_path):
    path = tmp_path / "conversation.db"
    first = ConversationStateStore(path, ttl_minutes=20)
    state = first.new(6, NOW)
    state.last_action = "create_task"
    state.last_task_id = "persistent-page"
    first.save(state, NOW)

    restarted = ConversationStateStore(path, ttl_minutes=20)
    loaded = restarted.get(6, NOW + timedelta(minutes=5))

    assert loaded.last_task_id == "persistent-page"
