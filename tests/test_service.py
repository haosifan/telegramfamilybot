from datetime import date, timedelta
from familybot.models import Task
from familybot.service import TaskService


def test_scope_today():
    today = date.today()
    tasks = [Task(title="a", due=today), Task(title="b", due=today + timedelta(days=1)), Task(title="c")]
    assert [x.title for x in TaskService._scope(tasks, "today")] == ["a"]


def test_format_urgent():
    text = TaskService.format_confirmation("Neu", Task(title="Paket", due=date(2026, 9, 7), priority="High", reminder="Urgent"))
    assert "Paket" in text and "dringend" in text and "07.09.2026" in text
