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
via REST (`POST /smallstack/api/webhooks/endpoints/`), MCP (`create_webhook`), or the CLI:

```bash
sc new webhook --name Zapier --target_url https://hooks.zapier.com/... \
   --event_filter '["support.ticket.*", "*.created"]' --user admin
```

`event_filter` holds fnmatch patterns (`[]` = inert). JSON fields accept native JSON on
every surface (a real array in REST/MCP payloads, a quoted JSON string on the CLI).
Omitted fields use the model defaults — a new endpoint is **enabled** with an
auto-generated signing secret. Pass `--secret <value>` (or `"secret"` in REST/MCP
payloads) to set a known secret; the field is **write-only** — it is never returned by
any read, only by the staff-gated Reveal action on the endpoint detail page.

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

```bash
sc new webhookreceiver --name Stripe --slug stripe --secret "whsec_..." --user admin
# → external systems POST to /webhooks/in/stripe/
```

(Or the UI at `/smallstack/webhooks/receivers/`, REST, or the `create_webhook_receiver`
MCP tool — same fields everywhere.) Omitted fields use the model defaults:
`require_signature=True`, `signature_header="X-Signature"`, `enabled=True`, and an
auto-generated `secret`. Set `--secret` when the provider hands you one (Stripe's
`whsec_…`); read a generated one back with the Reveal button on the receiver detail
page (staff-only, POST).

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
   --event_filter '["support.ticket.*"]' --user admin      # enabled by default
sc ls webhook                              # list endpoints ('sc ls' flags column: w = enable_webhooks)
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

**Test deliveries don't retry.** Every test path (`sc webhook test`, the detail-page
button, the `test_webhook` MCP tool) creates the delivery with `max_attempts=1`, so a
failed test goes **straight to `dead`** — it's a reachability probe, not a retry demo.
To watch retry/backoff, fire a real signal-driven event (save a model with
`enable_webhooks = True`) while the destination is down. Test and replay both print a
note when the target endpoint is disabled (test/replay sends still go out; signal
events don't).

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
   or receiver detail page (staff-only POST actions); set a known one at create/update
   time via the write-only `secret` field. It's never *returned* by REST/MCP/`sc` reads.
3. **SSRF guard.** Outbound targets can't be loopback/private IPs unless
   `SMALLSTACK_WEBHOOK_ALLOW_PRIVATE=true` (dev), and `SMALLSTACK_WEBHOOK_ALLOWLIST`
   restricts hosts. Checked at save time *and* send time.
4. **No opt-in ⇒ no fan-out.** A model without `enable_webhooks = True` fires nothing,
   even with a `["*"]` endpoint.
5. **Scripted creates honor model defaults.** `sc new` / REST `POST` / MCP `create_*`
   fill omitted fields from the model defaults, exactly like an ORM `.create()` —
   so a new endpoint/receiver is **enabled**, and a receiver keeps
   `require_signature=True` and `signature_header="X-Signature"` unless you say
   otherwise. Pass `--enabled=false` to create something switched off.
6. **`require_signature=False` fails open.** An enabled receiver with signature
   verification off accepts unsigned/bad-signature POSTs and still runs its handler —
   useful while onboarding a sender that doesn't sign yet, dangerous to leave on.
   `webhook_doctor` WARNs about every such receiver.
7. **REST lives under `/smallstack/api/`.** The generated endpoints are
   `/smallstack/api/webhooks/endpoints/`, `…/deliveries/`, `…/receivers/`,
   `…/receipts/` (browse them in Swagger at `/api/docs/`).

## Settings (config/settings/smallstack.py)

`SMALLSTACK_WEBHOOKS_ENABLED` / `_OUTBOUND` / `_INBOUND`, `SMALLSTACK_WEBHOOK_MAX_ATTEMPTS`,
`SMALLSTACK_WEBHOOK_TIMEOUT`, `SMALLSTACK_WEBHOOK_BACKOFF`, `SMALLSTACK_WEBHOOK_AUTO_DISABLE_AFTER`,
`SMALLSTACK_WEBHOOK_ALLOWLIST`, `SMALLSTACK_WEBHOOK_ALLOW_PRIVATE`, `SMALLSTACK_WEBHOOK_FAILURE_EMAILS`.
