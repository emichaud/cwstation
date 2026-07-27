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

## Foundation reshape (loop guard, seams, S2S, reliability)

- **Loop guard** (`context.py`): `suppress_webhooks()` thread-local context manager; inbound
  dispatch runs handlers inside it by default (`@webhook_handler(slug, cascade=True)` opts
  out); `X-SmallStack-Origin` on every delivery; `WebhookReceiver.ignore_origin` drops
  self-events.
- **Four extension seams** (`hooks.py`): `@webhook_transform` / `@webhook_auth` /
  `@webhook_verifier` / `@webhook_challenge`, named registries autodiscovered from
  `webhook_*.py`, each with a built-in default reproducing today's behavior. Selected by
  `endpoint.transform`/`auth_scheme` and `receiver.verifier`/`challenge`.
- **S2S pairing** (`services.pair_smallstack` + `sc webhook pair` + dashboard action):
  one-step loop-safe two-way link.
- **Reliability**: stable `event_id` (minted on signal, reused by replay, sent as
  `X-SmallStack-Event-Id`); `Retry-After` honored on 429/503; bulk dead-letter replay
  (`services.replay_dead`).
- **Envelope upgrade**: `event_id` + `origin` + `resource{type,id,url}` (absolute url),
  backward-compatible (original keys kept).
- **Reference adapter**: `contrib/eventgrid.py` — Azure Event Grid on all four seams, zero
  core edits.

## Surfaces

Four CRUDViews give admin + REST + MCP + `sc` for free; custom MCP tools add
`test_webhook`, `replay_delivery`, `replay_dead_deliveries`, `summary_deliveries`. Dashboard
at `/smallstack/webhooks/` (with “Connect a SmallStack” + “Replay all dead” actions),
a status monitor, `webhook_doctor` (wired into `sc doctor`).

**CLI:** endpoint/receiver CRUD via the generic verbs (`sc ls/new/set/rm webhook`); ops via
`manage.py webhook` (`status` / `list` / `test` / `replay` / `deliveries` / `tick`), also
fronted as `sc webhook <sub>`. The retry tick's cron entry point is `manage.py run_due_deliveries`.

## Files

`models.py` · `context.py` (loop guard) · `hooks.py` (four seams) · `signals.py` (outbound
event source) · `services.py` (sign / SSRF guard / fan-out / retry tick / replay / pair) ·
`tasks.py` (HTTP send + inbound dispatch) · `registry.py` (`@webhook_handler`) · `views.py`
(CRUDViews + receiver + actions + event-filter picker) · `contrib/eventgrid.py` (reference
adapter) · `mcp_tools.py` · `monitors.py` · `dashboard_widgets.py` ·
`management/commands/{webhook,run_due_deliveries,webhook_doctor}.py`

See `docs/skills/webhooks.md` for the how-to.
