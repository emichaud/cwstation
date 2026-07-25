# Skill: Webhooks (outbound + inbound)

Webhooks are the HTTP glue in the direction the rest of SmallStack doesn't cover.
Every built-in surface (CRUDView admin, REST, MCP, `sc`) is **inbound** — something
calls into the app. Webhooks add the **outbound** half (the app POSTs out when data
changes) and a matching **inbound** receiver (external systems POST in to trigger a
handler). Lives in `apps/webhooks/`.

## When to use which

| The user wants… | Use |
|---|---|
| "Notify Zapier / our other service when a `Ticket` changes" | **Outbound**: `enable_webhooks = True` on the CRUDView + register an endpoint |
| "Only fire on create, not update/delete" | Outbound: `webhook_events = ["created"]` on the CRUDView |
| "Stripe / GitHub should be able to POST events to us" | **Inbound**: a `WebhookReceiver` + a `@webhook_handler` |

## Outbound — 3 steps

### 1. Opt the model in (one line, like `enable_search`)

```python
# apps/support/views.py
class TicketCRUDView(CRUDView):
    model = Ticket
    enable_webhooks = True
    # optional — default is all three:
    webhook_events = ["created", "updated", "deleted"]
```

That's it for the *source*. A create/update/delete of `Ticket` through **any** surface
(HTML, REST, MCP, `sc`, or raw ORM) now emits an event
`support.ticket.created` (`<app_label>.<model>.<action>`). The payload reuses the same
`serialize()` the REST API emits, wrapped in an envelope:

```json
{ "event": "support.ticket.created", "action": "created",
  "model": "support.ticket", "id": 42, "occurred_at": "...",
  "data": { "id": 42, "title": "...", ... } }
```

### 2. Register an endpoint (the destination)

Endpoints are just data — create them in the UI at `/smallstack/webhooks/endpoints/`,
via REST/MCP (`create_webhook`), or in the shell:

```python
from apps.webhooks.models import WebhookEndpoint
WebhookEndpoint.objects.create(
    name="Zapier",
    target_url="https://hooks.zapier.com/...",
    event_filter=["support.ticket.*", "*.created"],  # fnmatch patterns; [] = inert
)
```

Every enabled endpoint whose `event_filter` matches gets one `WebhookDelivery`, POSTed
with an HMAC-SHA256 signature the receiver verifies:

```
X-SmallStack-Signature: sha256=<hex>   # HMAC(endpoint.secret, raw_body)
X-SmallStack-Event: support.ticket.created
X-SmallStack-Delivery: 123
```

### 3. Make sure the retry tick runs

Delivery failures retry with exponential backoff (`SMALLSTACK_WEBHOOK_BACKOFF`), driven
by a tick — **the framework has no automatic task retry, this is ours**. Pick exactly
one trigger per deployment (same choices as the scheduler):

- cron/systemd: `* * * * * python manage.py run_due_deliveries`
- localhost POST inside gunicorn: `POST /webhooks/tick/`
- fold `services.run_due_deliveries()` into your existing scheduler beat

After N consecutive failures (`SMALLSTACK_WEBHOOK_AUTO_DISABLE_AFTER`) an endpoint
auto-disables. Replay a dead delivery from its detail page or the `replay_delivery` MCP tool.

## Inbound — 2 steps

### 1. Register a receiver

```python
from apps.webhooks.models import WebhookReceiver
WebhookReceiver.objects.create(name="Stripe", slug="stripe", secret="whsec_...")
# → external systems POST to /webhooks/in/stripe/
```

The view verifies the signature (constant-time) against `secret`, using the header named
by `signature_header` (default `X-Signature`), records a `WebhookReceipt`, and returns
`202` fast (`401` on bad signature, `404` for unknown/disabled slug).

### 2. Write the handler

Put it in `apps/<yourapp>/webhook_handlers.py` (autodiscovered, like `mcp_tools.py`):

```python
from apps.webhooks.registry import webhook_handler

@webhook_handler("stripe")            # matches the receiver slug (or its `handler` field)
def on_stripe(receipt):
    event = receipt.json()            # parsed body, or None
    if event["type"] == "charge.succeeded":
        ...                           # do real work — runs in a background task
```

Raising inside a handler marks the receipt `failed` (recorded, not fatal).

## CLI

CRUD on endpoints/receivers uses the **generic** `sc` verbs (they're CRUDViews);
webhook-specific **ops** live under `sc webhook` (fronting `manage.py webhook`):

```bash
# create / manage endpoints (generic CRUD — audited + validated like the web UI)
sc new webhook --name Zapier --target_url https://hooks.zapier.com/... \
   --event_filter '["support.ticket.*"]' --enabled=true --user admin
sc ls webhook                              # list endpoints
sc set webhook 3 --enabled=false --user admin
sc rm  webhook 3 --force --user admin

# ops (report / test / replay / retry)
sc webhook status                          # counts by delivery status + retry backlog
sc webhook list                            # endpoints with health (last status, fail-streak)
sc webhook test Zapier                     # fire a signed test delivery
sc webhook deliveries --status dead --limit 20
sc webhook replay 42                       # re-send a dead delivery
sc webhook tick                            # run the retry tick once (interactive)
```

`sc webhook …` == `manage.py webhook …`; every subcommand takes `--json`.

## Validate

```bash
python manage.py webhook_doctor            # outbound registry, endpoint URLs, stuck retries, inbound handlers
python manage.py webhook_doctor --explain  # every enable_webhooks model + every handler
sc doctor webhook                          # same, via the framework CLI (also part of `sc doctor all`)
```

## Gotchas

1. **Empty `event_filter` is inert, not a firehose.** An endpoint with `[]` matches
   nothing — set `["*"]` deliberately for catch-all.
2. **The signing secret is stored recoverably, not hashed.** Unlike `APIToken`, an HMAC
   secret must be read back to sign every payload. Reveal/rotate it from the endpoint
   detail page; it's never exposed via REST/MCP.
3. **SSRF guard.** Outbound targets can't be loopback/private IPs unless
   `SMALLSTACK_WEBHOOK_ALLOW_PRIVATE=true` (dev), and `SMALLSTACK_WEBHOOK_ALLOWLIST`
   restricts hosts. Checked at save time *and* send time.
4. **No opt-in ⇒ no fan-out.** A model without `enable_webhooks = True` fires nothing,
   even with a `["*"]` endpoint.
5. **Creating an endpoint via CLI/REST is disabled by default.** `enabled` is a form
   `BooleanField`, so `sc new webhook` / a REST `POST` **without `enabled=true` yields a
   disabled endpoint** (unchecked checkbox = False). Pass `--enabled=true`, or flip it
   after with `sc set webhook <pk> --enabled=true`. The web create form has a checkbox,
   so this only bites the scripted paths.

## Settings (config/settings/smallstack.py)

`SMALLSTACK_WEBHOOKS_ENABLED` / `_OUTBOUND` / `_INBOUND`, `SMALLSTACK_WEBHOOK_MAX_ATTEMPTS`,
`SMALLSTACK_WEBHOOK_TIMEOUT`, `SMALLSTACK_WEBHOOK_BACKOFF`, `SMALLSTACK_WEBHOOK_AUTO_DISABLE_AFTER`,
`SMALLSTACK_WEBHOOK_ALLOWLIST`, `SMALLSTACK_WEBHOOK_ALLOW_PRIVATE`, `SMALLSTACK_WEBHOOK_FAILURE_EMAILS`.
