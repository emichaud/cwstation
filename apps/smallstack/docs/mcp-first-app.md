# Your first MCP app in 10 minutes

A linear walkthrough from `startapp` to Claude Desktop. Builds a tiny `apps/ticketing/` with two models, exposes both via MCP, verifies with `curl`, and connects Claude. If anything below surprises you, that's a documentation bug — file it.

Prereqs: a SmallStack checkout with the MCP foundations merged (any branch where `python manage.py mcp_doctor` exists).

## 1. Create the app (30 seconds)

```bash
uv run python manage.py startapp ticketing apps/ticketing
```

This gives you the standard `apps/ticketing/{__init__.py,apps.py,models.py,views.py,admin.py,tests.py,migrations/}` layout.

## 2. Define two models

```python
# apps/ticketing/models.py
from django.conf import settings
from django.db import models


class Customer(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Ticket(models.Model):
    STATUS_CHOICES = [
        ("open", "Open"),
        ("pending", "Pending"),
        ("resolved", "Resolved"),
    ]
    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("normal", "Normal"),
        ("urgent", "Urgent"),
    ]

    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="open")
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="normal")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="tickets")
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="assigned_tickets",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
```

## 3. Wire two CRUDViews in `views.py`

```python
# apps/ticketing/views.py
from django import forms

from apps.smallstack.crud import Action, CRUDView

from .models import Customer, Ticket


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "email"]


class CustomerCRUDView(CRUDView):
    model = Customer
    fields = ["name", "email"]
    list_fields = ["name", "email"]
    url_base = "customers"
    actions = [Action.LIST, Action.DETAIL]
    form_class = CustomerForm

    enable_mcp = True
    mcp_description = "Customers who can submit support tickets."
    mcp_singular = "customer"
    mcp_plural = "customers"


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ["title", "body", "status", "priority", "customer", "assignee"]


class TicketCRUDView(CRUDView):
    model = Ticket
    fields = ["title", "body", "status", "priority", "customer", "assignee"]
    list_fields = ["title", "status", "priority", "customer"]
    url_base = "tickets"
    actions = [Action.LIST, Action.CREATE, Action.DETAIL, Action.UPDATE, Action.DELETE]
    form_class = TicketForm
    search_fields = ["title", "body"]
    filter_fields = ["status", "priority", "customer", "assignee"]

    enable_mcp = True
    mcp_description = "Support tickets. Filter by status='open' to find unresolved work."
    mcp_singular = "ticket"
    mcp_plural = "tickets"
    # Inline FK details so the LLM doesn't need a second lookup.
    api_expand_fields = ["customer", "assignee"]
```

## 4. Register the app (one line in settings)

```python
# config/settings/base.py
INSTALLED_APPS = [
    # ...
    "apps.ticketing",
    "apps.mcp",
    # ...
]
```

That's it for wiring. `MCP_AUTODISCOVER` (default True) imports `apps/ticketing/views.py` at startup, which triggers `CRUDView.__init_subclass__` for both classes, which registers them — the factory runs right after and emits 7 MCP tools.

**Order in `INSTALLED_APPS` is irrelevant** when your CRUDViews live in `views.py` or `mcp_tools.py` — `apps.mcp.ready()` walks every installed app's modules itself, so it doesn't matter who appears first. Order *only* matters if you import the CRUDView from your own app's `ready()` (see next paragraph), because then *your* `ready()` must run before MCP's.

If your CRUDViews lived in a non-conventional module (e.g. `apps/ticketing/crud.py`), add a `ready()` import in `apps/ticketing/apps.py` AND list your app before `apps.mcp` in `INSTALLED_APPS`:

```python
class TicketingConfig(AppConfig):
    name = "apps.ticketing"
    def ready(self):
        from . import crud   # only needed if NOT in views.py / mcp_tools.py
```

## 5. Migrate

```bash
uv run python manage.py makemigrations ticketing
uv run python manage.py migrate
```

## 6. Seed a few rows

```bash
uv run python manage.py shell <<'PY'
from apps.ticketing.models import Customer, Ticket

acme = Customer.objects.create(name="Acme Corp",    email="ops@acme.example")
beta = Customer.objects.create(name="Beta Industries", email="it@beta.example")

Ticket.objects.create(title="Printer not responding",  status="open",     priority="urgent", customer=acme)
Ticket.objects.create(title="Laptop won't charge",     status="open",     priority="normal", customer=acme)
Ticket.objects.create(title="Email setup",             status="resolved", priority="low",    customer=beta)
print("seeded")
PY
```

## 7. Doctor + token

```bash
uv run python manage.py mcp_doctor
```

You should see `Server registry  7 tools registered` (2 for Customer + 5 for Ticket).

```bash
uv run python manage.py create_api_token admin --name dev --access-level readonly
# ← copy the printed raw key into TOKEN env var below
TOKEN="<paste here>"
```

## 8. Smoke-test the live server

In another terminal:

```bash
make run             # starts the dev server on :8005
```

Back in your first terminal:

```bash
make mcp-test
```

This mints a temp readonly token, hits `/mcp` for real (not the in-process test client), runs `tools/list` + a sample `tools/call`, then revokes the token. You should see `[✓] tools/list` and `[✓] tools/call list_customers` then `Result: PASS`. Exits 0 on success, 2 on connection failure, 4 on any RPC error.

## 9. Call a tool with a filter (curl)

For surgical testing — e.g. "does my `filter_fields = ["status", "priority"]` actually work?" — you can hand-craft the JSON-RPC request:

```bash
TOKEN=$(uv run python manage.py create_api_token admin --name dev --access-level readonly | awk 'NR==4{print $1}')

curl -s -X POST http://localhost:8005/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0","id":2,"method":"tools/call",
    "params":{"name":"list_tickets","arguments":{"status":"open","priority":"urgent"}}
  }' | jq '.result.content[0].text | fromjson | .results'
```

Returns the urgent open tickets. Note that `customer` comes back as `{"id": 1, "name": "Acme Corp"}` thanks to `api_expand_fields`.

## 10. Connect Claude Desktop

Settings → Connectors → Add custom connector → URL `http://localhost:8005/mcp`. Sign in when the consent page pops, click Allow.

Then ask Claude: *"What support tools do you have?"* and *"Show me the urgent open tickets."* If the first returns the seven names and the second returns the seeded rows, you're done.

## What you just learned

| Step | Concept |
|---|---|
| 3 | `enable_mcp = True` is the opt-in |
| 3 | `mcp_description` is written for the LLM |
| 3 | `mcp_singular`/`mcp_plural` give consistent tool names |
| 3 | `filter_fields` becomes typed input parameters on `list_*` |
| 3 | `api_expand_fields` makes FKs nested in the response |
| 4 | App order in `INSTALLED_APPS` matters — yours before `apps.mcp` |
| 4 | Autodiscover handles `views.py` and `mcp_tools.py`; explicit ready() import for anything else |
| 7 | `mcp_doctor` is your friend — green = working |
| 10 | The OAuth dance is automatic in the Connectors UI |

For deeper reference, see [`mcp.md`](mcp.md), [`mcp-enable-models.md`](mcp-enable-models.md), and [`mcp-architecture.md`](mcp-architecture.md).
