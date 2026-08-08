"""Seed sample rig photos for a development operator (DEVELOPMENT ONLY).

The product ships no manufacturer photos — operators upload their own on the
Rig Setup page (see apps/cw/models.py::CWRigPhoto). For local demos we want a
few examples present so the "your own photo" feature is visible right after
`make setup`. These images are *rendered by us* (clearly tagged SAMPLE PHOTO),
not real copyrighted product shots, and they live in MEDIA_ROOT (gitignored).

Idempotent: a (user, model) that already has a photo is left untouched, so a
real photo an operator uploaded is never clobbered. Pass --force to regenerate.

  uv run python manage.py cw_demo_photos            # seed for `admin`
  uv run python manage.py cw_demo_photos --user me  # a different operator
  uv run python manage.py cw_demo_photos --force    # overwrite existing
"""
from __future__ import annotations

import io
import math
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError, CommandParser

User = get_user_model()

W, H = 900, 540

# rig -> (catalog search substring, accent RGB, form factor)
RIGS: list[tuple[str, tuple[int, int, int], str]] = [
    ("TS-440", (233, 196, 106), "desk"),      # Kenwood TS-440S
    ("IC-7300", (79, 185, 80), "desk"),        # Icom IC-7300
    ("IC-705", (96, 165, 250), "compact"),     # Icom IC-705
    ("TH-D74", (191, 148, 255), "handheld"),   # Kenwood TH-D74
    ("IC-2730", (224, 108, 108), "mobile"),    # Icom IC-2730
]


def _font(sz: int, bold: bool = False):
    from PIL import ImageFont

    for path in (
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        ("/System/Library/Fonts/Supplemental/Courier New Bold.ttf"
         if bold else "/System/Library/Fonts/Supplemental/Courier New.ttf"),
    ):
        try:
            return ImageFont.truetype(path, sz)
        except OSError:
            continue
    return ImageFont.load_default()


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _vgrad(draw, box, top, bot) -> None:
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0)
        draw.line([(x0, y), (x1, y)], fill=_lerp(top, bot, t))


def _knob(draw, cx, cy, r, accent) -> None:
    for i in range(r, 0, -1):
        t = i / r
        draw.ellipse([cx - i, cy - i, cx + i, cy + i], fill=_lerp((70, 74, 82), (26, 28, 33), t))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(12, 13, 16), width=3)
    dx, dy = cx + int(math.sin(0) * (r - 10)), cy - (r - 10)
    draw.ellipse([dx - 5, dy - 5, dx + 5, dy + 5], fill=accent)


def _render(model_name: str, accent: tuple, form: str) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    _vgrad(d, (0, 0, W, H), (30, 33, 40), (14, 16, 20))
    _vgrad(d, (0, int(H * 0.62), W, H), (22, 24, 30), (10, 11, 14))

    if form == "handheld":
        bx0, by0, bx1, by1 = 350, 70, 550, 470
    elif form == "mobile":
        bx0, by0, bx1, by1 = 150, 210, 750, 360
    elif form == "compact":
        bx0, by0, bx1, by1 = 230, 190, 670, 380
    else:  # desk
        bx0, by0, bx1, by1 = 120, 170, 780, 400

    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ds = ImageDraw.Draw(sh)
    ds.rounded_rectangle([bx0 + 12, by1 - 8, bx1 + 24, by1 + 40], radius=30, fill=(0, 0, 0, 120))
    img.paste(Image.alpha_composite(img.convert("RGBA"), sh).convert("RGB"), (0, 0))
    d = ImageDraw.Draw(img)

    _vgrad(d, (bx0, by0, bx1, by1), (74, 79, 89), (34, 37, 44))
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=22, outline=(16, 18, 22), width=4)
    d.line([(bx0 + 14, by0 + 8), (bx1 - 14, by0 + 8)], fill=(120, 126, 138), width=2)

    pad = 26
    if form == "handheld":
        d.rounded_rectangle([bx1 - 60, by0 - 46, bx1 - 40, by0 + 6], radius=8, fill=(40, 43, 50))
        lcd = [bx0 + pad, by0 + pad, bx1 - pad, by0 + 150]
        d.rounded_rectangle(lcd, radius=10, fill=(6, 10, 8))
        d.text((lcd[0] + 18, lcd[1] + 40), "14.058", font=_font(46, True), fill=accent)
        ky = lcd[3] + 20
        for r in range(4):
            for c in range(3):
                kx = bx0 + pad + c * 58
                d.rounded_rectangle([kx, ky + r * 52, kx + 46, ky + r * 52 + 40], radius=8, fill=(48, 51, 59))
    else:
        lw = 300 if form in ("desk", "compact") else 260
        lcd = [bx0 + pad, by0 + pad, bx0 + pad + lw, by1 - pad]
        d.rounded_rectangle(lcd, radius=12, fill=(6, 10, 8))
        d.text((lcd[0] + 20, lcd[1] + 18), "14.058", font=_font(64, True), fill=accent)
        d.text((lcd[0] + 22, lcd[1] + 92), "CW   RIT  20W", font=_font(20, True),
               fill=_lerp(accent, (10, 12, 12), 0.35))
        for i in range(9):
            bh = 8 + i * 3
            bx = lcd[0] + 22 + i * 16
            col = accent if i < 6 else (70, 74, 82)
            d.rectangle([bx, lcd[3] - 18 - bh, bx + 10, lcd[3] - 18], fill=col)
        kcx = bx1 - (110 if form == "desk" else 80)
        kcy = (by0 + by1) // 2
        kr = min(70, (by1 - by0) // 2 - 16)
        _knob(d, kcx, kcy, kr, accent)
        for i in range(3):
            bx = lcd[2] + 22 + i * 44
            d.rounded_rectangle([bx, by1 - pad - 26, bx + 34, by1 - pad], radius=6, fill=(52, 55, 63))

    d.text((bx0 + 8, by1 + 14), model_name, font=_font(26, True), fill=(210, 214, 222))
    tag, tf = "SAMPLE PHOTO", _font(18, True)
    tw = d.textlength(tag, font=tf)
    d.rounded_rectangle([W - tw - 40, 20, W - 16, 52], radius=8, fill=(0, 0, 0))
    d.rounded_rectangle([W - tw - 40, 20, W - 16, 52], radius=8, outline=accent, width=2)
    d.text((W - tw - 28, 26), tag, font=tf, fill=accent)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=86)
    return buf.getvalue()


class Command(BaseCommand):
    help = "Seed sample rig photos for a dev operator (DEVELOPMENT ONLY)"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--user", default="admin", help="operator username (default: admin)")
        parser.add_argument("--force", action="store_true", help="overwrite existing photos")

    def handle(self, *args: Any, **opts: Any) -> None:
        # Same guard as create_dev_superuser: these are dev demo assets and the
        # Pillow render is pointless in prod. Never seed against a live DB.
        if not settings.DEBUG:
            raise CommandError("cw_demo_photos refuses to run with DEBUG=False (dev-only demo assets).")
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.stdout.write(self.style.WARNING("Pillow not installed — skipping demo photos."))
            return

        from apps.cw import rigdaemon
        from apps.cw.models import CWRigPhoto

        user = User.objects.filter(username=opts["user"]).first()
        if user is None:
            self.stdout.write(self.style.WARNING(
                f"No user '{opts['user']}' — run create_dev_superuser first. Skipping."))
            return

        models = rigdaemon.list_models()
        if not models:
            self.stdout.write(self.style.WARNING(
                "Hamlib catalog empty (rigctl not installed?) — skipping demo photos."))
            return

        made = skipped = 0
        for sub, accent, form in RIGS:
            match = next((m for m in models if sub.lower() in m["model"].lower()), None)
            if not match:
                continue
            existing = CWRigPhoto.objects.filter(user=user, rig_model=match["id"]).first()
            if existing and not opts["force"]:
                skipped += 1
                continue
            name = f"{match['mfg']} {match['model']}"
            data = _render(name, accent, form)
            photo = existing or CWRigPhoto(user=user, rig_model=match["id"])
            if existing:
                photo.image.delete(save=False)
            photo.image.save(f"{match['id']}.jpg", ContentFile(data), save=True)
            self.stdout.write(self.style.SUCCESS(f"  ✓ {name} (model {match['id']})"))
            made += 1

        self.stdout.write(
            f"Demo rig photos for '{user.username}': {made} written, {skipped} already present."
        )
