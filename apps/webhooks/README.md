# apps/webhooks

Outbound event delivery + inbound receivers — the HTTP glue that complements
SmallStack's inbound-only surfaces (CRUDView / REST / MCP / `sc`).

## Outbound

A model opts in with `enable_webhooks = True` on its CRUDView (like `enable_search`).
A global `post_save`/`post_delete` observer (`signals.py`, modeled on `apps/search/signals.py`)
catches the change across **every** surface incl. raw ORM, fans it out to matching
`WebhookEndpoint`s, and enqueues a signed HTTP POST (`tasks.deliver_webhook`). Failures
retry with exponential backoff via `services.run_due_deliveries` — the framework has no
automatic task retry, so we own it (atomic-claim tick, borrowed from the scheduler).

- `WebhookEndpoint` — config (URL, secret, `event_filter`, enabled, counters)
- `WebhookDelivery` — append-only per-attempt record (status, `next_attempt_at`, response)

## Inbound

`WebhookReceiver` (config) exposes `/webhooks/in/<slug>/`. The view (`views.incoming_webhook`)
verifies the HMAC signature, records a `WebhookReceipt`, and dispatches to a handler
registered with `@webhook_handler("<slug>")` (in any app's `webhook_handlers.py`,
autodiscovered). `WebhookReceipt` is the append-only log.

## Surfaces

Four CRUDViews give admin + REST + MCP + `sc` for free; custom MCP tools add
`test_webhook`, `replay_delivery`, `summary_deliveries`. Dashboard at `/smallstack/webhooks/`,
a status monitor, `webhook_doctor` (wired into `sc doctor`).

**CLI:** endpoint/receiver CRUD via the generic verbs (`sc ls/new/set/rm webhook`); ops via
`manage.py webhook` (`status` / `list` / `test` / `replay` / `deliveries` / `tick`), also
fronted as `sc webhook <sub>`. The retry tick's cron entry point is `manage.py run_due_deliveries`.

## Files

`models.py` · `signals.py` (outbound event source) · `services.py` (sign / SSRF guard /
fan-out / retry tick) · `tasks.py` (HTTP send + inbound dispatch) · `registry.py`
(`@webhook_handler`) · `views.py` (CRUDViews + receiver + actions) · `mcp_tools.py` ·
`monitors.py` · `dashboard_widgets.py` · `management/commands/{webhook,run_due_deliveries,webhook_doctor}.py`

See `docs/skills/webhooks.md` for the how-to.
