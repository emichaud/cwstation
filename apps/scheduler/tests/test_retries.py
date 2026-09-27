"""Scheduler x task retries: ``max_retries`` pins a budget; runs follow the chain.

A scheduled run records its *first* attempt's result id. With retries, that
attempt can fail while a later one succeeds, so the scheduler reads the run's
outcome from the latest attempt (``apps.tasks.reliability.chain_outcome``):
the run stays QUEUED while a retry is pending, and the failure email goes out
once, after the final attempt.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.tasks import task
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from apps.scheduler import services
from apps.scheduler.forms import ScheduledJobForm
from apps.scheduler.models import ScheduledJob, ScheduledJobRun
from apps.scheduler.views import ScheduledJobCRUDView
from apps.tasks.models import TaskAttempt
from apps.tasks.reliability import maybe_retry

pytestmark = pytest.mark.django_db


@task
def plain_task(**kwargs):  # no @retry_policy: only a pinned budget retries it
    return kwargs


TASK_PATH = "apps.scheduler.tests.test_retries.plain_task"


@pytest.fixture(autouse=True)
def db_backend(settings):
    settings.TASKS = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend", "QUEUES": ["default", "email"]}}
    settings.SMALLSTACK_TASKS_BACKOFF_SECONDS = 0
    settings.SMALLSTACK_TASKS_BACKOFF_MAX_SECONDS = 0


@pytest.fixture
def mails(settings, monkeypatch):
    settings.SMALLSTACK_SCHEDULER_FAILURE_EMAILS = ["ops@example.com"]
    calls: list[dict] = []

    class _FakeTask:
        def enqueue(self, **kw):
            calls.append(kw)

    monkeypatch.setattr("apps.tasks.tasks.send_email_task", _FakeTask())
    return calls


def _fire(**kw) -> ScheduledJobRun:
    kw.setdefault("name", "job")
    job = ScheduledJob.objects.create(
        task_path=TASK_PATH, schedule_type="interval", interval_spec="1h", enabled=True, **kw
    )
    ScheduledJob.objects.filter(pk=job.pk).update(next_run_at=timezone.now() - timedelta(minutes=1))
    services.run_due_jobs()
    return job.runs.get()


def _fail(result_id, *, error="OSError: smtp down", finished_ago: float = 0) -> None:
    DBTaskResult.objects.filter(id=result_id).update(
        status="FAILED",
        finished_at=timezone.now() - timedelta(seconds=finished_ago),
        exception_class_path="builtins.OSError",
        traceback=f"Traceback...\n{error}",
    )


def _succeed(result_id, value) -> None:
    DBTaskResult.objects.filter(id=result_id).update(
        status="SUCCESSFUL", finished_at=timezone.now(), return_value=value
    )


def test_max_retries_pins_the_chain_budget():
    run = _fire(max_retries=2)
    pinned = TaskAttempt.objects.get(result_id=run.task_result_id)
    assert pinned.max_attempts == 3 and pinned.attempt == 1


def test_zero_max_retries_pins_nothing():
    run = _fire(max_retries=0)
    assert not TaskAttempt.objects.filter(result_id=run.task_result_id).exists()


def test_run_stays_queued_while_retrying_then_reports_the_retry_success(mails):
    run = _fire(name="flaky-job", max_retries=2)
    _fail(run.task_result_id)

    services.reconcile_run_outcomes()  # failed, retry on its way (signal/sweep)
    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.QUEUED

    retry_id = maybe_retry(run.task_result_id)
    services.reconcile_run_outcomes()  # retry READY
    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.QUEUED

    _succeed(retry_id, {"sent": 1})
    services.reconcile_run_outcomes()
    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.SUCCESS
    assert run.message.endswith("(attempt 2)")
    assert ScheduledJob.objects.get(pk=run.job_id).last_status == ScheduledJobRun.Status.SUCCESS
    assert mails == []


def test_exhausted_chain_fails_once_with_the_last_error(mails):
    run = _fire(name="doomed", max_retries=1)
    _fail(run.task_result_id, error="OSError: first")
    retry_id = maybe_retry(run.task_result_id)
    _fail(retry_id, error="OSError: second")

    services.reconcile_run_outcomes()
    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.FAILED
    assert run.message == "after 2 attempts: OSError: second"
    assert len(mails) == 1
    assert "Error: after 2 attempts: OSError: second" in mails[0]["message"]

    services.reconcile_run_outcomes()
    assert len(mails) == 1  # terminal runs aren't re-reconciled


def test_a_retry_nobody_will_make_is_final():
    """Failed long ago with budget left but no retry: past the sweep's window."""
    run = _fire(max_retries=2)
    _fail(run.task_result_id, finished_ago=2 * 3600)

    services.reconcile_run_outcomes()
    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.FAILED


def test_no_budget_fails_on_the_first_attempt_as_before(mails):
    run = _fire(max_retries=0)
    _fail(run.task_result_id)

    services.reconcile_run_outcomes()
    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.FAILED
    assert run.message == "OSError: smtp down"
    assert len(mails) == 1


def test_overlap_guard_waits_for_a_retrying_run():
    run = _fire(name="no-overlap", max_retries=2, allow_overlap=False)
    _fail(run.task_result_id)
    job = ScheduledJob.objects.get(pk=run.job_id)
    ScheduledJob.objects.filter(pk=job.pk).update(next_run_at=timezone.now() - timedelta(minutes=1))

    result = services.run_due_jobs()

    assert result.skipped == 1 and result.enqueued == 0


def test_max_retries_is_editable_and_capped():
    assert "max_retries" in ScheduledJobCRUDView.fields
    job = ScheduledJob.objects.create(name="edit-me", task_path=TASK_PATH, schedule_type="interval", interval_spec="1h")
    data = {
        "name": job.name,
        "task_path": TASK_PATH,
        "kwargs": "{}",
        "queue_name": "default",
        "schedule_type": "interval",
        "interval_spec": "1h",
        "catch_up": "run_once",
        "enabled": "on",
    }
    assert ScheduledJobForm(data={**data, "max_retries": 3}, instance=job).is_valid()
    form = ScheduledJobForm(data={**data, "max_retries": 11}, instance=job)
    assert not form.is_valid() and "max_retries" in form.errors
