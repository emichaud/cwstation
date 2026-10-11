"""A WorkerHeartbeat row identifies a process incarnation, not a pool member.

Deliberately a *new* migration rather than an edit to the unreleased
``0001_initial``: this one emits real DDL, so a database that already applied
``0001`` (a harness clone, a branch tester) must be able to move forward. Only
``help_text``-shaped changes are safe to fold into an unreleased migration.

The ``RunPython`` drops pre-upgrade rows. They are keyed on the operator's
``--worker-id`` label, so a pre-upgrade ``RUNNING`` task whose ``worker_ids[-1]``
is that bare label would still match one *exactly* — and if the row happened to
carry ``stopped_at``, the task would be reaped the instant this migration lands.
Dropping the rows sends every pre-upgrade claim down the conservative
``ORPHAN_SECONDS`` path instead, which is the intended upgrade behaviour: neither
instantly reapable nor permanently unreapable. Nothing is lost — the table is
ephemeral liveness state that ``reaper.prune_heartbeats`` already deletes on a
timer, any worker running during a migration is being restarted anyway, and the
next beat writes a fresh row.
"""

from django.db import migrations, models


def drop_pre_upgrade_rows(apps, schema_editor):
    """Pre-upgrade rows are label-keyed; retire them so claims fall to orphan."""
    apps.get_model("tasks", "WorkerHeartbeat").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="workerheartbeat",
            name="label",
            field=models.CharField(blank=True, db_index=True, help_text="The operator's --worker-id.", max_length=64),
        ),
        migrations.AlterField(
            model_name="workerheartbeat",
            name="worker_id",
            field=models.CharField(
                help_text="label#token — matches worker_ids[-1].", max_length=80, unique=True
            ),
        ),
        migrations.RunPython(drop_pre_upgrade_rows, migrations.RunPython.noop, elidable=True),
    ]
