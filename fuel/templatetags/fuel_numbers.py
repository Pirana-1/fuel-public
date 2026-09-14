from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


def _format_number(value, decimals=3):
    if value in (None, ""):
        return ""
    try:
        number = Decimal(str(value))
        places = int(decimals)
    except (InvalidOperation, TypeError, ValueError):
        return value

    formatted = f"{number:,.{places}f}"
    formatted = formatted.replace(",", "_").replace(".", ",").replace("_", ".")
    if "," in formatted:
        formatted = formatted.rstrip("0").rstrip(",")
    return formatted


@register.filter
def number_tr(value, decimals=3):
    return _format_number(value, decimals)


@register.filter
def liters_tr(value, decimals=3):
    return _format_number(value, decimals)
