"""Audit trail: automatic for every model registered in AUDITED_MODELS, explicit for business actions."""
from decimal import Decimal
import datetime

from django.db.models.signals import post_delete, post_save, pre_save
from django.forms.models import model_to_dict

from .middleware import current_ip, current_user


def _jsonable(d):
    out = {}
    for k, v in d.items():
        if isinstance(v, (Decimal,)):
            v = str(v)
        elif isinstance(v, (datetime.date, datetime.datetime)):
            v = v.isoformat()
        elif hasattr(v, "pk"):
            v = v.pk
        elif isinstance(v, (list, tuple)):
            v = [getattr(x, "pk", x) for x in v]
        out[k] = v
    return out


def log(action, obj=None, summary="", before=None, after=None, model=None, object_id=None):
    from .models import AuditLog

    AuditLog.objects.create(
        user=current_user(),
        ip=current_ip(),
        action=action,
        model=model or (obj._meta.label if obj is not None else ""),
        object_id=str(object_id if object_id is not None else getattr(obj, "pk", "")),
        summary=(summary or str(obj or ""))[:300],
        before=before,
        after=after,
    )


def _snapshot(instance):
    try:
        return _jsonable(model_to_dict(instance))
    except Exception:  # pragma: no cover - defensive
        return None


def _pre_save(sender, instance, **kwargs):
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).first()
        instance._audit_before = _snapshot(old) if old else None
    else:
        instance._audit_before = None


def _post_save(sender, instance, created, **kwargs):
    after = _snapshot(instance)
    before = getattr(instance, "_audit_before", None)
    if not created and before == after:
        return
    log("create" if created else "update", instance, before=before, after=after)


def _post_delete(sender, instance, **kwargs):
    log("delete", instance, before=_snapshot(instance))


def register(*models):
    for m in models:
        pre_save.connect(_pre_save, sender=m, dispatch_uid=f"audit_pre_{m._meta.label}")
        post_save.connect(_post_save, sender=m, dispatch_uid=f"audit_post_{m._meta.label}")
        post_delete.connect(_post_delete, sender=m, dispatch_uid=f"audit_del_{m._meta.label}")
