"""P2 surface tests — dashboard render, tick localhost guard, run-now, stat drills."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.scheduler.models import ScheduledJob, ScheduledJobRun

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def staff(client):
    u = User.objects.create_user(username="staff", password="pw", is_staff=True)
    client.force_login(u)
    return u


@pytest.fixture
def job():
    return ScheduledJob.objects.create(
        name="Nightly", task_path="apps.tasks.tasks.process_data_task",
        schedule_type="cron", cron_expression="0 2 * * *",
    )


def test_dashboard_renders_for_staff(client, staff, job):
    resp = client.get(reverse("scheduler_dashboard"))
    assert resp.status_code == 200
    assert b"Scheduler" in resp.content


def test_dashboard_requires_staff(client):
    resp = client.get(reverse("scheduler_dashboard"))
    assert resp.status_code in (302, 403)  # redirected to login or forbidden


def test_stat_detail_active(client, staff, job):
    resp = client.get(reverse("scheduler_stat_detail", args=["active"]))
    assert resp.status_code == 200
    assert b"Nightly" in resp.content


def test_tick_rejects_non_localhost(client):
    resp = client.post(reverse("scheduler_tick"), REMOTE_ADDR="10.0.0.5")
    assert resp.status_code == 403


def test_tick_allows_localhost(client):
    resp = client.post(reverse("scheduler_tick"), REMOTE_ADDR="127.0.0.1")
    assert resp.status_code == 200
    assert "enqueued" in resp.json()


def test_run_now_enqueues(client, staff, job):
    resp = client.post(reverse("scheduler_run_now", args=[job.pk]))
    assert resp.status_code == 302  # redirects to dashboard
    job.refresh_from_db()
    assert job.total_runs == 1
    assert ScheduledJobRun.objects.filter(job=job).exists()


def test_run_now_requires_staff(client, job):
    resp = client.post(reverse("scheduler_run_now", args=[job.pk]))
    assert resp.status_code in (302, 403)
    job.refresh_from_db()
    assert job.total_runs == 0


def test_run_now_returns_to_next_url(client, staff, job):
    """The job's control page posts its own path so the operator stays there."""
    edit_url = reverse("scheduler/jobs-update", args=[job.pk])
    resp = client.post(reverse("scheduler_run_now", args=[job.pk]), {"next": edit_url})
    assert resp.status_code == 302
    assert resp.url == edit_url


def test_run_now_rejects_offsite_next(client, staff, job):
    """`next` must not become an open redirect — offsite falls back to the dashboard."""
    resp = client.post(
        reverse("scheduler_run_now", args=[job.pk]), {"next": "https://evil.example/phish"}
    )
    assert resp.status_code == 302
    assert resp.url == reverse("scheduler_dashboard")


def test_edit_page_shows_identity_strip_with_run_now(client, staff, job):
    """The control page leads with the job's identity and Run now at the top."""
    resp = client.get(reverse("scheduler/jobs-update", args=[job.pk]))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "sje-strip" in body
    assert "Nightly" in body
    assert "Run now" in body
    # the strip's form posts back to this page
    assert f'name="next" value="{reverse("scheduler/jobs-update", args=[job.pk])}"' in body
    # identity is no longer duplicated by the old read-only section
    assert "What it runs" not in body
