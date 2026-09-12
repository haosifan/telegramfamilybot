from datetime import date, timedelta

import pytest

from familybot.llm import ACTION_PLAN_SCHEMA
from familybot.models import Action, ActionPlan, Task
from test_dialog import NOW, StubParser, run, service


def existing_task():
    return Task(id="phone-page", title="Telefon besorgen", due=date(2026, 9, 15))


def test_a_without_due_updates_existing_task(tmp_path):
    svc, store, _ = service(tmp_path, [existing_task()])
    parser = StubParser(ActionPlan(actions=[Action(action="create_task", title="Telefon besorgen")]))

    result = run(svc.handle_message(20, "Telefon besorgen, ohne Termin", parser, NOW))

    assert "Geändert" in result
    assert store.updated == [("phone-page", {"due": None})]
    assert store.created == [] and store.deleted == []


def test_b_deadline_delete_collapses_wrong_multi_action_plan_to_update(tmp_path):
    svc, store, _ = service(tmp_path, [existing_task()])
    parser = StubParser(ActionPlan(actions=[
        Action(action="create_task", title="Telefon besorgen"),
        Action(action="delete_task", task_query="Deadline"),
    ]))

    result = run(svc.handle_message(21, "Telefon besorgen. Deadline löschen", parser, NOW))

    assert "Geändert" in result
    assert store.updated == [("phone-page", {"due": None})]
    assert store.created == [] and store.deleted == []


def test_c_due_in_one_week_is_resolved_and_updated(tmp_path):
    svc, store, _ = service(tmp_path, [existing_task()])
    parser = StubParser(ActionPlan(actions=[Action(action="create_task", title="Telefon besorgen")]))

    run(svc.handle_message(22, "Telefon besorgen, Termin in eine Woche", parser, NOW))

    assert store.updated == [("phone-page", {"due": NOW.date() + timedelta(days=7)})]
    assert store.created == []


def test_d_day_month_uses_next_plausible_date(tmp_path):
    svc, store, _ = service(tmp_path, [existing_task()])
    parser = StubParser(ActionPlan(actions=[Action(action="create_task", title="Telefon besorgen")]))

    run(svc.handle_message(23, "Telefon besorgen, 18.09.", parser, NOW))

    assert store.updated == [("phone-page", {"due": date(2026, 9, 18)})]
    assert store.created == []


def test_e_delete_task_still_deletes_the_task_itself(tmp_path):
    svc, store, _ = service(tmp_path, [existing_task()])
    parser = StubParser(ActionPlan(actions=[Action(action="create_task", title="Telefon besorgen")]))

    result = run(svc.handle_message(24, "Telefon besorgen löschen", parser, NOW))

    assert "Gelöscht" in result
    assert store.deleted == ["phone-page"]
    assert store.updated == [] and store.created == []


def test_f_prefixed_deadline_delete_is_an_update(tmp_path):
    svc, store, _ = service(tmp_path, [existing_task()])
    parser = StubParser(ActionPlan(actions=[Action(action="delete_task", task_query="Deadline")]))

    run(svc.handle_message(25, "bei Telefon besorgen die Deadline löschen", parser, NOW))

    assert store.updated == [("phone-page", {"due": None})]
    assert store.deleted == []


def test_g_plain_duplicate_is_still_blocked(tmp_path):
    svc, store, _ = service(tmp_path, [existing_task()])
    parser = StubParser(ActionPlan(actions=[Action(action="create_task", title="Telefon besorgen")]))

    result = run(svc.handle_message(26, "Telefon besorgen", parser, NOW))

    assert "existiert bereits" in result
    assert store.created == [] and store.updated == []


@pytest.mark.parametrize(
    ("text", "parsed", "expected_changes"),
    [
        (
            "Telefon besorgen, Priorität hoch",
            Action(action="create_task", title="Telefon besorgen", priority="High"),
            {"priority": "High"},
        ),
        (
            "Telefon besorgen, morgen",
            Action(action="create_task", title="Telefon besorgen"),
            {"due": NOW.date() + timedelta(days=1)},
        ),
        (
            "Telefon besorgen, ohne Termin",
            Action(action="create_task", title="Telefon besorgen"),
            {"due": None},
        ),
    ],
)
def test_h_metadata_updates_bypass_duplicate_protection(tmp_path, text, parsed, expected_changes):
    svc, store, _ = service(tmp_path, [existing_task()])
    parser = StubParser(ActionPlan(actions=[parsed]))

    run(svc.handle_message(27, text, parser, NOW))

    assert store.updated == [("phone-page", expected_changes)]
    assert store.created == []


def test_structured_output_uses_explicit_clear_fields():
    properties = ACTION_PLAN_SCHEMA["properties"]["actions"]["items"]["properties"]
    required = ACTION_PLAN_SCHEMA["properties"]["actions"]["items"]["required"]

    assert properties["clear_fields"]["items"]["enum"] == ["due", "reminder", "notes"]
    assert "clear_fields" in required
    assert "clear_due" not in properties


def test_attribute_delete_never_falls_back_to_deleting_attribute_named_task(tmp_path):
    deadline = Task(id="deadline-page", title="Deadline")
    svc, store, _ = service(tmp_path, [deadline])
    parser = StubParser(ActionPlan(actions=[Action(action="delete_task", task_query="Deadline")]))

    result = run(svc.handle_message(28, "Unbekannte Aufgabe. Deadline löschen", parser, NOW))

    assert "keine offene Aufgabe" in result
    assert store.deleted == [] and store.updated == []
