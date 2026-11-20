"""Item names: two active items may not carry the same name. «فراخ كاملة» and «فراخ  كامله» are the same item to the
shop owner, so names are compared after trimming, collapsing spaces, dropping tashkeel / tatweel and folding the alef,
ya and ta-marbuta variants (a recipe linked to an empty twin would cost 0)."""
import re

from django.core.exceptions import ValidationError

_FOLD = str.maketrans("أإآٱىةؤئ", "اااايهوي")


def norm(name):
    t = re.sub(r"[ً-ْـ]", "", str(name or ""))
    t = re.sub(r"\s+", " ", t.translate(_FOLD)).strip().lower()
    return t


def clean(name, n=150):
    """The name as it is saved: trimmed, single spaces."""
    return re.sub(r"\s+", " ", str(name or "")).strip()[:n]


def index(exclude=None):
    """{normalised name: Item} of the active items (sizes of a product carry the product's name, so they are left out)."""
    from .models import Item

    qs = Item.objects.filter(active=True, variant_of__isnull=True).exclude(code="0")
    if exclude:
        qs = qs.exclude(pk__in=exclude if isinstance(exclude, (list, tuple, set)) else [exclude])
    out = {}
    for pk, nm in qs.values_list("pk", "name"):
        out.setdefault(norm(nm), (pk, nm))
    return out


def clash(name, exclude=None, idx=None):
    """The name of the active item that already carries this name (normalised), or None."""
    key = norm(name)
    if not key:
        return None
    got = (idx if idx is not None else index(exclude)).get(key)
    return got[1] if got else None


def check(name, exclude=None, idx=None):
    """Refuses a duplicate with a message that says what to do."""
    other = clash(name, exclude, idx)
    if other:
        raise ValidationError(f"يوجد صنف باسم «{other}» بالفعل — استخدم الصنف الموجود أو عدّله، أو اكتب اسمًا يميّزه")
