"""Retries with backoff (``reliability.py``, ``signals.py``, reaper hand-off).

Retries need the real engine (``ImmediateBackend`` can't defer and never sends
the ``db_worker`` signal), so these tests switch ``TASKS`` to
``DatabaseBackend`` and drain the queue with SmallStack's ``db_worker --batch``
in-process. Backoff is 0 for the test tasks so a retry is due immediately and
the same batch run picks it up.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import call_command
from django.tasks import task
from django.utils import timezone
from django_tasks.base import TaskResultStatus
from django_tasks.signals import task_finished
from django_tasks_db.management.commands.db_worker import Worker
from django_tasks_db.models import DBTaskResult

from apps.tasks import reliability, signals
from apps.tasks.models import TaskAttempt, WorkerHeartbeat
from apps.tasks.reaper import reap_lost_tasks
from apps.tasks.reliability import RetryPolicy, maybe_retry, pin_max_attempts, policy_for, retry_policy

DB_TASKS = {
    "default": {
        "BACKEND": "django_tasks_db.DatabaseBackend",
        "QUEUES": ["default", "email"],
    }
}

CALLS = {"n": 0}


# Module level, not nested: the worker re-imports tasks by dotted path.
@retry_policy(max_attempts=3, backoff=0, backoff_max=0)
@task
def flaky(fail_times: int) -> str:
    CALLS["n"] += 1
    if CALLS["n"] <= fail_times:
        raise OSError(f"transient #{CALLS['n']}")
    return "ok"


@retry_policy(max_attempts=3, retry_on=(OSError,), backoff=0, backoff_max=0)
@task
def picky() -> None:
    raise ValueError("permanent")


@task
def no_policy() -> None:
    raise OSError("no policy")


@retry_policy(max_attempts=3, retry_lost=False, backoff=0, backoff_max=0)
@task
def not_on_lost() -> None:
    raise OSError("x")


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _engine(settings):
    settings.TASKS = DB_TASKS
    settings.SMALLSTACK_TASKS_BACKOFF_SECONDS = 0
    settings.SMALLSTACK_TASKS_BACKOFF_MAX_SECONDS = 0
    CALLS["n"] = 0


def _run_worker() -> None:
    with mock.patch.object(Worker, "configure_signals"):
        call_command("db_worker", batch=True, startup_delay=False, queue_name="*", worker_id="t-worker", verbosity=0)


def _rows(task_obj) -> list[DBTaskResult]:
    return list(DBTaskResult.objects.filter(task_path=task_obj.module_path).order_by("enqueued_at"))


def _failed_row(task_obj, *, exc_path: str = "builtins.OSError", finished_ago: float = 0) -> DBTaskResult:
    now = timezone.now()
    return DBTaskResult.objects.create(
        status=TaskResultStatus.FAILED,
        args_kwargs={"args": [], "kwargs": {}},
        task_path=task_obj.module_path,
        backend_name="default",
        queue_name="default",
        run_after=now,
        started_at=now,
        finished_at=now - timedelta(seconds=finished_ago),
        exception_class_path=exc_path,
        traceback=f"Traceback...\n{exc_path.rsplit('.', 1)[-1]}: boom",
    )


class TestWorkerRetries:
    def test_fails_twice_then_succeeds_as_one_chain(self):
        first = flaky.enqueue(fail_times=2)
        _run_worker()

        rows = _rows(flaky)
        assert [r.status for r in rows] == ["FAILED", "FAILED", "SUCCESSFUL"]
        assert "transient #1" in rows[0].traceback and "transient #2" in rows[1].traceback
        attempts = list(TaskAttempt.objects.order_by("attempt"))
        assert [(a.attempt, a.cause) for a in attempts] == [(2, "failed"), (3, "failed")]
        assert {a.root_id for a in attempts} == {rows[0].id}
        assert str(rows[0].id) == str(first.id)
        assert attempts[0].parent_id == rows[0].id and attempts[1].parent_id == rows[1].id

    def test_stops_at_max_attempts(self):
        flaky.enqueue(fail_times=10)
        _run_worker()
        assert [r.status for r in _rows(flaky)] == ["FAILED"] * 3

    def test_retry_on_filters_exceptions(self):
        picky.enqueue()
        _run_worker()
        assert [r.status for r in _rows(picky)] == ["FAILED"]

    def test_no_policy_means_no_retry_by_default(self):
        no_policy.enqueue()
        _run_worker()
        assert len(_rows(no_policy)) == 1

    def test_default_max_attempts_setting_applies_to_undecorated_tasks(self, settings):
        settings.SMALLSTACK_TASKS_DEFAULT_MAX_ATTEMPTS = 2
        no_policy.enqueue()
        _run_worker()
        assert len(_rows(no_policy)) == 2

    def test_retry_keeps_queue_priority_and_args(self):
        flaky.using(priority=7).enqueue(fail_times=1)
        _run_worker()
        first, second = _rows(flaky)
        assert (second.priority, second.queue_name, second.backend_name) == (7, "default", "default")
        assert second.args_kwargs == first.args_kwargs

    def test_a_raising_retry_check_never_hurts_the_worker(self):
        """signals.py's contract: the receiver swallows everything."""
        flaky.enqueue(fail_times=0)
        flaky.enqueue(fail_times=5)
        with mock.patch.object(signals, "maybe_retry", side_effect=RuntimeError("boom")):
            _run_worker()
        statuses = sorted(r.status for r in _rows(flaky))
        assert statuses == ["FAILED", "SUCCESSFUL"]  # both ran; no retry; no crash

    def test_receiver_ignores_successful_results(self):
        result = mock.Mock(status=TaskResultStatus.SUCCESSFUL, id="x")
        with mock.patch.object(signals, "maybe_retry") as retry:
            task_finished.send(sender=object, task_result=result)
        retry.assert_not_called()


class TestMaybeRetry:
    def test_schedules_the_retry_after_the_backoff(self):
        row = _failed_row(flaky)
        now = timezone.now()
        with mock.patch.object(RetryPolicy, "delay", return_value=120.0):
            new_id = maybe_retry(row.id, now=now)
        new = DBTaskResult.objects.get(id=new_id)
        assert new.status == "READY"
        assert abs((new.run_after - now).total_seconds() - 120) < 1

    def test_only_one_retry_per_attempt(self):
        row = _failed_row(flaky)
        assert maybe_retry(row.id) is not None
        assert maybe_retry(row.id) is None
        assert TaskAttempt.objects.filter(parent_id=row.id).count() == 1
        assert len(_rows(flaky)) == 2

    def test_ignores_non_failed_and_missing_rows(self):
        row = _failed_row(flaky)
        DBTaskResult.objects.filter(id=row.id).update(status="SUCCESSFUL")
        assert maybe_retry(row.id) is None
        assert maybe_retry("00000000-0000-0000-0000-000000000000") is None

    def test_force_stop_is_classified_interrupted(self):
        row = _failed_row(flaky, exc_path="builtins.SystemExit")
        new_id = maybe_retry(row.id)
        assert TaskAttempt.objects.get(result_id=new_id).cause == "interrupted"

    def test_retry_lost_false_skips_lost_and_interrupted(self):
        assert maybe_retry(_failed_row(not_on_lost, exc_path="builtins.SystemExit").id) is None
        assert maybe_retry(_failed_row(not_on_lost, exc_path="apps.tasks.exceptions.WorkerLost").id) is None
        assert maybe_retry(_failed_row(not_on_lost).id) is not None

    def test_unimportable_exception_matches_only_catch_all(self):
        assert maybe_retry(_failed_row(flaky, exc_path="gone.module.Err").id) is not None
        assert maybe_retry(_failed_row(picky, exc_path="gone.module.Err").id) is None

    def test_pinned_budget_overrides_the_policy_and_follows_the_chain(self):
        row = _failed_row(no_policy)
        pin_max_attempts(row.id, 3)
        second = maybe_retry(row.id)
        assert TaskAttempt.objects.get(result_id=second).max_attempts == 3
        DBTaskResult.objects.filter(id=second).update(status="FAILED", exception_class_path="builtins.OSError")
        third = maybe_retry(second)
        assert TaskAttempt.objects.get(result_id=third).attempt == 3
        DBTaskResult.objects.filter(id=third).update(status="FAILED", exception_class_path="builtins.OSError")
        assert maybe_retry(third) is None


class TestReaperAndRepair:
    def test_reaped_task_is_retried_as_lost(self):
        now = timezone.now()
        WorkerHeartbeat.objects.create(worker_id="dead", last_seen_at=now, stopped_at=now)
        row = DBTaskResult.objects.create(
            status="RUNNING",
            args_kwargs={"args": [], "kwargs": {"fail_times": 0}},
            task_path=flaky.module_path,
            backend_name="default",
            queue_name="default",
            run_after=now,
            started_at=now,
            worker_ids=["dead"],
        )

        result = reap_lost_tasks()

        assert result.retried == 1
        child = TaskAttempt.objects.get(parent_id=row.id)
        assert child.cause == "lost" and child.attempt == 2
        _run_worker()
        assert DBTaskResult.objects.get(id=child.result_id).status == "SUCCESSFUL"

    def test_repair_sweep_retries_a_missed_failure(self):
        missed = _failed_row(flaky, finished_ago=300)
        fresh = _failed_row(flaky, finished_ago=1)  # inside the grace window
        ancient = _failed_row(flaky, finished_ago=2 * 3600)  # outside the window
        unretryable = _failed_row(no_policy, finished_ago=300)

        assert reliability.repair_missed_retries() == 1

        assert TaskAttempt.objects.filter(parent_id=missed.id).exists()
        for row in (fresh, ancient, unretryable):
            assert not TaskAttempt.objects.filter(parent_id=row.id).exists()
        assert reliability.repair_missed_retries() == 0  # idempotent

    def test_repair_sweep_includes_pinned_chains(self):
        row = _failed_row(no_policy, finished_ago=300)
        pin_max_attempts(row.id, 2)
        assert reliability.repair_missed_retries() == 1


class TestPolicy:
    def test_backoff_doubles_with_jitter_and_caps(self):
        policy = RetryPolicy(backoff=10, backoff_max=100)
        for _ in range(50):
            assert 5 <= policy.delay(1) <= 10
            assert 20 <= policy.delay(3) <= 40
            assert 50 <= policy.delay(10) <= 100

    def test_decorator_validation(self):
        with pytest.raises(ValueError):
            retry_policy(max_attempts=0)
        with pytest.raises(TypeError):
            retry_policy(max_attempts=2)(lambda: None)

    def test_email_tasks_opt_in(self):
        for name in ("send_email_task", "send_html_email_task", "send_welcome_email"):
            policy = policy_for(f"apps.tasks.tasks.{name}")
            assert policy.attempts() == 3 and policy.retry_on == (OSError,)
        assert policy_for("apps.tasks.tasks.process_data_task").attempts() == 1
