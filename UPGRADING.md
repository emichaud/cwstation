# Upgrading SmallStack

Breaking changes and the migration steps for each. Downstream projects (smallstack_web,
opshugger, and any clone) should read the section for any version they cross when pulling
upstream.

Most releases are non-breaking patch/minor bumps and won't appear here. If a release isn't
listed, no downstream migration is required.

---

## v0.14.0 — Django 6.1 + email `MAILERS` (breaking **only** if you set `EMAIL_BACKEND`)

**Who is affected:** downstream projects that define **`EMAIL_BACKEND`** (or any `EMAIL_HOST` /
`EMAIL_PORT` / `EMAIL_USE_TLS` / … *setting*) in their own `config/settings/*.py`. If you only set
these via **environment variables**, you're fine — nothing to do.

**Why:** SmallStack upgraded to **Django 6.1**, which consolidates email config into a single
[`MAILERS`](https://docs.djangoproject.com/en/6.1/topics/email/) dict (like `DATABASES`/`CACHES`) and
deprecates the flat `EMAIL_*` settings (removed in Django 7.0). The base settings now ship `MAILERS`.
Django 6.1 **raises `ImproperlyConfigured` if both `MAILERS` and a deprecated `EMAIL_*` setting are
defined** — so a downstream that still sets `EMAIL_BACKEND` in a settings module will fail to boot with:

> `Deprecated email settings are not allowed when MAILERS is defined: EMAIL_BACKEND.`

**Migration — replace the `EMAIL_*` settings with a `MAILERS` override:**

```python
# before (in your settings module)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.example.com"
EMAIL_USE_TLS = True

# after
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {"host": "smtp.example.com", "use_tls": True},
    },
}
```

Or drop your override entirely and set the `EMAIL_*` **env vars** — SmallStack's
`config/settings/_email.py:build_mailers()` reads them into `MAILERS` for you (dev defaults to the
console backend, production to SMTP). `DEFAULT_FROM_EMAIL` / `SERVER_EMAIL` are unchanged (not
deprecated). `send_mail()`, `EmailMultiAlternatives`, and `mail_admins()` work exactly as before.

**Also removed upstream:** the deprecated `fail_silently=` argument on all framework mail calls (it's
removed in Django 7.0). If your own code passes `fail_silently=True`, wrap the send in `try/except`
instead; `fail_silently=False` is the default, so just drop it.

## v0.13.0 — Runbook app (additive, non-breaking)

**Who is affected:** everyone upgrading from v0.12.x. No manual steps are required — this entry is
informational.

**What's new:** the runbook dynamic-document system ships as a new `smallstack_runbook` app (documents
with immutable versioning, retention, subscriptions, and web / REST / MCP / CLI surfaces over one
service layer).

**Migrations:** `make migrate` applies ten new migrations automatically
(`smallstack_runbook.0001_initial` … `0010_runbook_is_public_runbook_owner`). This was verified with an
upgrade test — cloned v0.12.4, seeded data, merged v0.13.0, and migrated forward on the populated DB:
migrations applied cleanly, **no data loss**, `manage.py check` passed.

**New settings (all optional, safe defaults — no action needed):**

- `RUNBOOK_BASE_TEMPLATE`, `RUNBOOK_STAFF_REQUIRED` (default `True`), and the
  `RUNBOOK_GENERATED_*` retention caps — see `config/settings/smallstack.py`.
- `TRUST_PROXY_HEADERS` (default `False`) — only enable behind a trusted proxy that sets
  `X-Forwarded-For` (kamal-proxy does).

**Evidence:** upgrade path and full quality assessment in the v0.13.0 report card
([`docs/report-cards/`](docs/report-cards/)).

---

## v0.12.0 — `django-tables2` removed (BREAKING)

**Who is affected:** any downstream project that defined its own `tables.Table` subclass and
wired it to a CRUDView with `table_class = MyTable`, or imported from `apps.smallstack.tables`
(`ActionsColumn`, `BooleanColumn`, `DetailLinkColumn`).

**Symptom on merge/upgrade:** after `uv sync` drops `django_tables2`, the project fails to
import before any test runs:

```
ModuleNotFoundError: No module named 'django_tables2'
# and / or
ImportError: cannot import name 'ActionsColumn' from 'apps.smallstack.tables'
```

`apps/smallstack/tables.py` has been deleted; framework apps (usermanager, heartbeat,
explorer) moved to the `TableDisplay` / `{% crud_table %}` flow in the same release, so the
base stays green — the breakage only surfaces in *your* app's imports.

**Find affected sites:**

```bash
grep -rn "django_tables2\|apps.smallstack.tables\|table_class" apps/
```

**Migration:** replace the `Table` class with declarative attributes on the CRUDView.

```python
# BEFORE — apps/<app>/tables.py + views.py
class PortfolioTable(tables.Table):
    title = DetailLinkColumn(url_base="manage/portfolio", link_view="update")
    is_published = BooleanColumn()
    updated_at = tables.DateTimeColumn(format="M d, Y")
    actions = ActionsColumn(url_base="manage/portfolio")

class PortfolioCRUDView(CRUDView):
    table_class = PortfolioTable

# AFTER — views.py only (delete tables.py)
def _render_solution_type(value, obj):
    return format_html('<span class="badge">{}</span>', obj.get_solution_type_display())

class PortfolioCRUDView(CRUDView):
    list_fields = ["title", "solution_type", "is_published", "display_order", "updated_at"]
    link_field = "title"   # clickable -> detail (needs Action.DETAIL in actions)
    field_transforms = {"solution_type": _render_solution_type}
```

`TableDisplay` now handles automatically — no column class needed:

| Old column class | Now done by |
|---|---|
| choice display (`get_FOO_display()`) | automatic for choice fields |
| `BooleanColumn` | automatic ✓ / — for booleans |
| `DateTimeColumn(format=...)` | automatic localized datetime with tooltip |
| `ActionsColumn` | derived from the CRUDView's `actions` |
| `ActionsColumn` subclass (per-row filtering) | override `CRUDView.row_actions(cls, obj, request, default_actions)` |
| custom cell HTML | a `field_transforms` entry — a registered transform name, or a `(value, obj) -> str \| mark_safe` callable |

After migrating, remove `django-tables2` from your own `pyproject.toml` if you pinned it, and
delete the now-unused `apps/<app>/tables.py`.

### Also in v0.12.0 (additive): the status-monitoring subsystem

v0.12.0 also ships the pluggable status-monitoring system — `/smallstack/status/`, the
branded public `/status/` board, Site/External monitors, per-monitor SLA, and three
site-level **surface toggles** (`SMALLSTACK_PUBLIC_STATUS_ENABLED` /
`SMALLSTACK_API_ENABLED` / `SMALLSTACK_MCP_ENABLED`). It's **additive** — no breaking
change — but it touches shared config (`config/urls.py`, `config/settings/smallstack.py`)
and moves the CRUDView `views` autodiscover into `SmallStackConfig.ready()`. For the
merge integration points and the one gotcha (keep that autodiscover call or Search goes
empty), see **`docs/skills/merge-0.12.0.md`**.
