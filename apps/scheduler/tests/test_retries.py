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
from apps.tasks import reliability
from apps.tasks.models import TaskAttempt
from apps.tasks.reliability import maybe_retry, retry_policy

pytestmark = pytest.mark.django_db


@task
def plain_task(**kwargs):  # no @retry_policy: only a pinned budget retries it
    return kwargs


# A task whose *developer* opted into repeating it, including after a worker
# death. Only such a task can reach the reaped-then-completed case at all: a
# pinned budget alone will not re-run a WorkerLost attempt (IMPLICIT_PINNED_POLICY).
@retry_policy(max_attempts=3, backoff=0, backoff_max=0)
@task
def repeatable_task(**kwargs):
    return kwargs


TASK_PATH = "apps.scheduler.tests.test_retries.plain_task"
REPEATABLE_PATH = "apps.scheduler.tests.test_retries.repeatable_task"


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


def _fire(*, task_path: str = TASK_PATH, **kw) -> ScheduledJobRun:
    kw.setdefault("name", "job")
    job = ScheduledJob.objects.create(
        task_path=task_path, schedule_type="interval", interval_spec="1h", enabled=True, **kw
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


def test_max_retries_does_not_rerun_a_run_whose_worker_died():
    """Ticking "Retries on failure" must not silently opt a task into at-least-once.

    `plain_task` declares no `@retry_policy`, so nobody asserted it is safe to
    repeat. A reaped (WorkerLost) attempt may have already done half its work.
    """
    run = _fire(max_retries=2)
    DBTaskResult.objects.filter(id=run.task_result_id).update(
        status="FAILED",
        finished_at=timezone.now(),
        exception_class_path="apps.tasks.exceptions.WorkerLost",
        traceback="WorkerLost: worker w1 exited while this task was running",
    )

    assert maybe_retry(run.task_result_id, "lost") is None
    assert not TaskAttempt.objects.filter(parent_id=run.task_result_id).exists()

    # ...and the run is final rather than sitting QUEUED waiting for a retry
    # that will never come.
    services.reconcile_run_outcomes()
    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.FAILED


def test_max_retries_help_text_names_both_behaviours():
    """The operator-facing control states what it does and does not retry."""
    help_text = ScheduledJob._meta.get_field("max_retries").help_text
    assert "when the task raises" in help_text
    assert "worker died mid-task is not re-run" in help_text


def test_a_run_that_executed_twice_says_so(mails):
    """The scheduler must not record one run for two side effects.

    Attempt 1 is declared lost and retried; the retry runs; then the paused
    worker resumes and completes attempt 1 too. Both executed. A run message of
    just "(attempt 2)" would tell the operator this job ran once.
    """
    run = _fire(name="ran-twice", task_path=REPEATABLE_PATH, max_retries=2)
    _fail(run.task_result_id, error="WorkerLost: worker w1 exited")
    retry_id = maybe_retry(run.task_result_id, "lost")
    # The retry starts before the paused worker comes back...
    DBTaskResult.objects.filter(id=retry_id).update(status="RUNNING")
    # ...and then attempt 1 completes anyway.
    _succeed(run.task_result_id, {"sent": 1})
    reliability.record_late_completion(run.task_result_id)
    _succeed(retry_id, {"sent": 1})

    services.reconcile_run_outcomes()
    run.refresh_from_db()

    assert run.status == ScheduledJobRun.Status.SUCCESS
    assert "ran more than once" in run.message
    assert "attempt 1 completed (SUCCESSFUL)" in run.message
    assert len(run.message) <= 255


def test_a_cancelled_retry_does_not_make_a_successful_run_look_failed():
    """The retry never started, so the run succeeded once — no alarm, no email."""
    run = _fire(name="cancelled-retry", task_path=REPEATABLE_PATH, max_retries=2)
    _fail(run.task_result_id, error="WorkerLost: worker w1 exited")
    retry_id = maybe_retry(run.task_result_id, "lost")
    _succeed(run.task_result_id, {"sent": 1})
    reliability.record_late_completion(run.task_result_id)
    assert DBTaskResult.objects.get(id=retry_id).status == "FAILED"  # cancelled

    services.reconcile_run_outcomes()
    run.refresh_from_db()

    assert run.status == ScheduledJobRun.Status.SUCCESS
    assert "ran more than once" not in run.message


def test_the_double_execution_note_survives_a_long_summary():
    """Truncation must cut the summary, never the warning."""
    note = "ran more than once: attempt 1 completed (SUCCESSFUL) after being declared lost"
    message = services._with_double_execution_note("x" * 400, note)
    assert len(message) <= 255
    assert message.endswith(note)


def test_the_max_retries_cap_is_advertised_not_just_enforced():
    """MCP and OpenAPI clients must be able to discover the bound before calling.

    Enforcement was always correct (the model validator runs via full_clean for
    the form, REST and MCP alike), but both schemas are built from the *form*
    field's `max_value`, and a ModelForm-generated field has None there — so the
    schema advertised `minimum` and no `maximum`.
    """
    from apps.mcp.factory import _build_form_input_schema

    prop = _build_form_input_schema(ScheduledJobCRUDView)["properties"]["max_retries"]
    assert prop == {"type": "integer", "minimum": 0, "maximum": ScheduledJob.MAX_RETRIES_CAP}


def test_the_cap_has_one_source():
    """Model validator, form bound and widget attribute must not drift apart."""
    from django.core.validators import MaxValueValidator

    cap = ScheduledJob.MAX_RETRIES_CAP
    model_field = ScheduledJob._meta.get_field("max_retries")
    maxima = [v.limit_value for v in model_field.validators if isinstance(v, MaxValueValidator)]
    assert maxima == [cap]  # PositiveSmallIntegerField also contributes a MinValueValidator(0)
    form_field = ScheduledJobForm().fields["max_retries"]
    assert form_field.max_value == cap
    assert form_field.widget.attrs["max"] == cap
    # Declaring the field explicitly must not have changed its required-ness.
    assert form_field.required is True


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
