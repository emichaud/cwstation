# Skill: iOS-style collection view (CW list screens)

**Read this before building or restyling a record-list screen** (like Sessions or the
Logbook). It's the CW Station house pattern for turning a `CRUDView` list into a clean
**collection of cards** — a responsive flow grid of tiles — with a **Table toggle** and a
**prominent search + filter-toggle toolbar**. It builds on `card-displays.md` (the
`CardDisplay` mechanism) and `cw-screen-design.md` (the overall house style).

Canonical implementations to copy from:
- **Sessions** — `SessionCardDisplay` in `apps/cw/views.py`, `templates/cw/session_card.html`, `session_list.html`.
- **Logbook** — `QSOCardDisplay` in `apps/cw/views.py`, `templates/cw/qso_card.html`, `log_list.html`.

## The four pieces

### 1. Backend — a CardDisplay subclass + a Table peer

```python
from apps.smallstack.displays import CardDisplay, TableDisplay

class ThingCardDisplay(CardDisplay):
    name = "cards"
    supports_bulk = False               # no per-row checkboxes on a tile grid
    item_template = "cw/thing_card.html"

    def build_card(self, obj, cfg, request):
        from django.urls import reverse
        return {                        # everything the tile needs, pre-shaped
            "title": obj.headline,
            "edit_url": reverse("cw/thing-update", args=[obj.pk]),   # or detail_url
            "delete_url": reverse("cw/thing-delete", args=[obj.pk]),
            # …clean, display-ready values (format dates/rst/etc. here, not in the template)
        }

class ThingCRUDView(CRUDView):
    displays = [ThingCardDisplay(), TableDisplay()]   # cards default; table is the toggle
    filter_fields = ["direction", "source"]           # backend quick-filters (exact match)
    paginate_by = 5
    # keep list_fields/link_field/field_transforms — they drive the Table view
```

Notes:
- The list view reads `?display=` to pick the active display (`_get_active_display`), so a
  `display` radio in the toolbar switches Cards↔Table server-side.
- `filter_fields` does **exact-match** filtering on `?<field>=` for choice/char fields —
  that's what the toggle chips drive.
- The card's `detail_url`/`edit_url` is set in `build_card`; when the view has no DETAIL
  action, build the update URL yourself (the Logbook does this).

### 2. Item template — a clean tile

Keep it a self-contained tile: a link over the body + a hover action cluster **as a sibling
of the link** (never nest `<a>` inside `<a>`). Delete uses the CRUD modal.

```django
{% load theme_tags %}
<div class="cw-thing-tile">
  <a href="{{ card.edit_url }}" class="cw-tile-link" aria-label="…">
    <span class="cw-tile-top"><span class="cw-tile-title">{{ card.title }}</span> …badges…</span>
    …meta rows…
  </a>
  <div class="cw-thing-actions">
    <button type="button" class="cw-thing-act cw-thing-act-del"
            onclick="crudDeleteModal(this, '{{ card.title }}')" data-delete-url="{{ card.delete_url }}">🗑</button>
  </div>
</div>
```
Include `smallstack/crud/includes/delete_modal.html` in the page so `crudDeleteModal` exists.

### 3. The toolbar — one htmx form (search + filter toggles + view switch + count)

Replace the framework `list_toolbar.html` include with a custom form. Everything is one
`<form>` so search, filters, and the view switch combine cleanly:

```django
<form id="list-toolbar" class="cw-sess-toolbar"
      hx-get="{% url 'cw/thing-list' %}"   {# CLEAN path — see gotcha below #}
      hx-target="#crud-list-content" hx-swap="innerHTML swap:150ms" hx-push-url="true"
      hx-trigger="change, keyup delay:300ms from:input[name='q'], search from:input[name='q']">
  <input type="search" name="q" value="{{ request.GET.q|default:'' }}">
  {# toggle chips = real radios styled as segmented controls (.cw-toggle/.cw-chip) #}
  <label class="cw-chip"><input type="radio" name="direction" value="" {% if not request.GET.direction %}checked{% endif %}><span>All</span></label>
  <label class="cw-chip"><input type="radio" name="direction" value="tx" {% if request.GET.direction == 'tx' %}checked{% endif %}><span>Sent</span></label>
  {# view switch #}
  <label class="cw-chip"><input type="radio" name="display" value="cards" {% if request.GET.display != 'table' %}checked{% endif %}><span>▦ Cards</span></label>
  <label class="cw-chip"><input type="radio" name="display" value="table" {% if request.GET.display == 'table' %}checked{% endif %}><span>▤ Table</span></label>
  <span id="list-toolbar-count" class="list-toolbar-count">
    <span class="list-toolbar-count-value">{{ toolbar_total_count|default:0 }}</span>
    <span class="list-toolbar-count-label">Record{{ toolbar_total_count|pluralize }}</span>
  </span>
</form>
<div id="crud-list-content">{% include "smallstack/crud/object_list_content.html" %}</div>
```

Reusable CSS lives in `cw.css`: `.cw-sess-toolbar`, `.cw-sess-search`, `.cw-toggle`,
`.cw-chip` (+ `.cw-chip-rx`/`.cw-chip-tx` for semantic colours), `.cw-view-toggle`.

### 4. CSS — the flow grid + tile

The framework renders cards into `.card-grid`. Make it an iOS collection with an
auto-fill flow grid, scoped to the page's body class:

```css
body.cw-thing .card-grid {
    grid-template-columns: repeat(auto-fill, minmax(264px, 1fr));
    gap: 14px; align-items: stretch;
}
```
Then style your tile (`.cw-thing-tile`) as a clean rounded card with hover lift; reveal
the action cluster on `:hover` (and always on `@media (hover: none)`).

## Gotchas (all learned the hard way)

- **`hx-get` must point at the clean list URL** (`{% url '…-list' %}`), *not* `""`. With
  `""` htmx appends the form params to the *current* URL, so each interaction accumulates
  duplicate `?q=&display=…&q=&display=…` params. The clean path replaces them.
- **Mirror the OOB count.** `object_list_content.html` sends an out-of-band refresh to
  `#list-toolbar-count` with "Record(s)" wording — match that markup/wording in your
  toolbar so the count doesn't flicker to a different label on the first htmx swap.
- **`toolbar_total_count`** (post-filter count) is in context when `search_fields`/
  `filter_fields` are set — use it, and it agrees with the OOB refresh.
- **Scope the grid with a two-class selector** (`body.cw-console.cw-logbook`) when two
  collection pages share `cw-console` but need different tile widths — equal-specificity
  single-class rules otherwise fight by source order.
- **Toggles are real radios**, server-rendered `checked` from `request.GET`, so filters
  **deep-link and survive reload**. Don't do client-only toggling.
- **Full-width vs tiles:** Sessions started full-width (long decoded copy needs width) then
  moved to tiles per request; the copy tile clamps to 3 lines (`-webkit-line-clamp`). Pick
  the grid `minmax` to fit the content (Logbook 264px; a copy-heavy list wants more).
