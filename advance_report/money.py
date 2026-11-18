"""Деньги: только Decimal, ровно до копейки."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

KOPECK = Decimal("0.01")
ZERO = Decimal("0.00")


def quantize(value: Decimal) -> Decimal:
    rounded = value.quantize(KOPECK, rounding=ROUND_HALF_UP)
    if rounded == 0:
        return ZERO
    return rounded


def parse_amount(raw: str) -> Decimal:
    text = raw.strip()
    if text == "":
        raise ValueError("пустая сумма")
    if any(ch in text for ch in ", eE"):
        raise ValueError(f"сумма {text!r} нужна с точкой, без пробелов и без экспоненты")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"сумма {text!r} не число") from exc
    if not value.is_finite():
        raise ValueError(f"сумма {text!r} не число")
    if value < 0:
        raise ValueError(f"сумма {text!r} меньше нуля")
    if value != value.quantize(KOPECK):
        raise ValueError(f"у суммы {text} больше двух знаков после точки, сами не округляем")
    return value.quantize(KOPECK)


def format_money(value: Decimal) -> str:
    """Текст для человека: 100 000,00 или -5 000,00."""
    rounded = quantize(value)
    sign = "-" if rounded < 0 else ""
    whole, frac = f"{abs(rounded):.2f}".split(".")
    groups: list[str] = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    return sign + " ".join(reversed(groups)) + "," + frac


def format_money_csv(value: Decimal) -> str:
    """Число для Excel в русской локали: 100000,00."""
    return f"{quantize(value):.2f}".replace(".", ",")
