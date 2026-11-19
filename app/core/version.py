"""Trio version — raise it with every release published to clients (Trio License «نشر تحديث»)."""
VERSION = "1.23.1"


def parse(v):
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except ValueError:
        return (0,)


def newer(a, b=VERSION):
    return parse(a) > parse(b)
