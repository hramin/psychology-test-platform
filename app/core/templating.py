"""Jinja2 templating setup (RTL Persian)."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Persian (Eastern Arabic) digit rendering — used for question numbers, ages, etc.
_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_num(value) -> str:
    return str(value).translate(_PERSIAN_DIGITS)


templates.env.filters["fa"] = fa_num
