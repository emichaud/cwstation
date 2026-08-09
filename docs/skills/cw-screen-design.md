# Skill: CW Station screen design

**Read this before building or restyling any operator-facing screen in CW Station.**
It captures the house style used across the recent redesigns (Send Setup, Callbook,
Rig Setup, Decode, the auth screens) so new screens match on the first try. It sits on
top of two framework skills — read those for the underlying rules:

- `modern-dark-theme.md` — the palette-variable rules (never hard-code a colour).
- `card-displays.md` — the `CardDisplay`/`TableDisplay` mechanism.

For **list** screens (Sessions, Logbook) use the iOS collection pattern in its own
skill: **`cw-collection-view.md`**. This file covers everything else — the **setup/tool
page** archetype and the shared conventions.

## The two archetypes

Every CW screen is one of two shapes. Pick the matching one; don't invent a third.

| Archetype | Used for | Pattern |
|---|---|---|
| **Setup / tool page** | Settings, forms, consoles — Send Setup, Callbook, Rig Setup, Decode | `cw-setup` cards (this file) |
| **Collection list** | Record lists — Sessions, Logbook | iOS collection view (`cw-collection-view.md`) |

## Setup / tool page recipe

The look: a centered single column of large, iconed cards, roomy inputs, big buttons —
readable for an older ham audience. Built from `cw-setup` primitives in `apps/cw/static/cw/cw.css`.

```django
{% block body_class %}cw-console cw-setup{% endblock %}
{% block extra_css %}<link rel="stylesheet" href="{% static 'cw/cw.css' %}">{% endblock %}

{% block content %}
<div class="cw-setup-wrap">
    <header class="cw-setup-head">
        <h2>Short imperative title</h2>
        <p>One sentence on what this page is for and where its settings apply.</p>
    </header>

    <section class="cw-setup-card">
        <div class="cw-step-head"><span class="cw-step-num">🔎</span><h3>Section name</h3></div>
        <p class="cw-setup-note">A muted line of guidance. {tags} and <code>code</code> read well here.</p>

        <div class="cw-cb-actions">   {# flex row: input(s) + button #}
            <input type="text" class="cw-setup-input cw-mono" placeholder="…">
            <button type="button" class="cw-btn-lg">Primary</button>
            <button type="button" class="cw-btn-lg cw-btn-danger">Destructive</button>
        </div>
    </section>
</div>
{% endblock %}
```

### The primitives (all in cw.css, all palette-safe)

| Class | What it is |
|---|---|
| `cw-setup-wrap` | Centered `max-width: 940px` column |
| `cw-setup-head` | Page intro — `h2` (1.9rem) + muted `p` |
| `cw-setup-card` | A large rounded card (`--card-bg`/`--card-border`) |
| `cw-step-head` + `cw-step-num` | Iconed section header — put an **emoji** in `cw-step-num` (⚙ 📻 🔑 🔄 🎧 ⌨️ 🧩 🏷 🔎) |
| `cw-setup-note` | Muted guidance paragraph |
| `cw-setup-input` | Large (48px) input; gold focus ring |
| `cw-btn-lg` / `cw-btn-xl` | Big primary button (gold). `cw-btn-ghost` = outlined secondary; `cw-btn-danger` = red |
| `cw-defaults` / `cw-default-knob` | Two big slider "knobs" side-by-side (see Send Setup, Decode) |
| `cw-badge`, `cw-badge-call`/`-tx`/`-rx`/`-mode` | Small pill badges |
| `.cw-dropzone` | Drag-and-drop file upload (Decode's recording upload) |

Reach for small layout helpers `cw-cb-actions` (flex row), `cw-cb-grid2` (two-col inputs),
`cw-cb-result` (status line) rather than inline styles.

### Rules

- **Never hard-code a colour.** Surfaces `var(--card-bg)`, accent `var(--primary)`, hero
  bands `var(--accent-band-bg)`, state `var(--success-fg)/--warning-fg/--error-fg`. (See
  `modern-dark-theme.md`.) The gold you see is the **`gold` brand palette**, not a hex.
- **Keep IDs stable when restyling.** The Callbook/Decode redesigns kept every element
  `id` so the existing JS kept working — only the wrapper markup/classes changed.
- **Preserve function.** After restyling a page, verify its JS still fires (screenshot +
  a DOM check that the key controls exist and a submit/action works).

## Auth screens (login / signup / reset)

They don't extend the app base; they extend `registration/auth_base.html`. CW Station
**overrides that template** at `templates/registration/auth_base.html` (project template,
no framework edit) to: load `cw.css`, default anonymous auth to the **gold** brand palette
(`window.SMALLSTACK = { colorPalette: 'gold' }` so `theme.js` keeps it — a stored user
preference still wins), and add a `cw-login` body class. `cw.css` then styles the
`.auth-*` classes into the setup look (roomy card, big rounded inputs, full-width button,
stacked gold footer links). All auth pages inherit it. To restyle auth, edit those two
places — not the individual login/signup templates.

## Shared gotchas (these bit us repeatedly)

- **Multi-line `{# … #}` comments render as literal text** (and shove the layout).
  Django's `{# #}` is single-line only — use `{% comment %}…{% endcomment %}` for anything
  spanning lines. `make lint` runs `scripts/check_django_comments.py`; run it after editing
  templates.
- **The dev server caches templates.** A *brand-new* template file (e.g. a new project
  override) isn't picked up until you restart `make run`. Existing-file edits hot-reload.
- **Screenshot to verify, across palettes.** `screenshot_auth` + `shot-scraper`, then read
  the PNG. A page that's right on `gold`/`django` but brown on `orange` means a hard-coded
  colour — grep for hex literals.

## Canonical examples to copy from

- **Setup pages:** `apps/cw/templates/cw/send.html` (Send Setup), `callbook.html`, `rig_setup.html`, `decode.html`.
- **Auth:** `templates/registration/auth_base.html` + the `body.cw-login` block in `cw.css`.
- **Collection lists:** see `cw-collection-view.md` (Sessions, Logbook).
