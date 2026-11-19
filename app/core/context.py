from .models import CompanySettings


def company(request):
    try:
        c = CompanySettings.get()
    except Exception:  # table may not exist during first migrate
        c = None
    from django.conf import settings

    pending, owner = 0, False
    u = getattr(request, "user", None)
    if c is not None and u is not None and u.is_authenticated:
        try:
            from pos.models import ChangeRequest
            from pos.voids import is_owner

            owner = is_owner(u)
            if owner:
                pending = ChangeRequest.objects.filter(state="pending").count()
        except Exception:
            pending = 0
    from .modules import enabled

    try:
        mods = enabled(c) if c is not None else set()
    except Exception:  # noqa: BLE001
        mods = set()
    from .modules import BUSINESS

    business_label = BUSINESS.get(getattr(c, "business", "") or "", ("",))[0] if c is not None else ""
    br = {}
    if c is not None and u is not None and u.is_authenticated and c.setup_done:
        try:
            from pos import branches as BR
            from pos.models import Branch

            if BR.branches_on():
                br = dict(branch_on=True, branch_view=BR.view_branch(request), branch_locked=bool(BR.home_branch(u)),
                          branch_list=list(Branch.objects.filter(active=True).order_by("id")))
        except Exception:  # noqa: BLE001
            br = {}
    upd = {}
    if c is not None and c.setup_done and u is not None and u.is_authenticated:
        from ui.upgrade import notice

        upd = notice(request)
    from .version import VERSION

    try:
        from .feed import newer_available

        newer_v = newer_available()
    except Exception:  # noqa: BLE001
        newer_v = ""
    act_items = "الأصناف"
    is_pharmacy = False
    try:
        if c is not None and isinstance(c.activity, dict):
            act_items = c.activity.get("items") or act_items
            business_label = c.activity.get("name") or business_label
            is_pharmacy = c.activity.get("key") == "pharmacy"
    except Exception:  # noqa: BLE001
        pass
    ctx = {**br, **upd, "act_items": act_items, "is_pharmacy": is_pharmacy, "app_version": VERSION, "newer_version": newer_v, "business_label": business_label, "company": c, "training": getattr(settings, "TRAINING", False), "pending_requests": pending, "is_owner": owner, "mods": mods}
    from gaming.services import MIN_CHOICES, ROUND_CHOICES

    ctx.update(g_round_choices=ROUND_CHOICES, g_min_choices=MIN_CHOICES)
    try:
        from . import menu

        ctx["W"] = menu.words(c) if c is not None else menu.WORDS["default"]
        if c is not None and u is not None and u.is_authenticated:
            ctx["menu"] = menu.build(request, ctx)
            if request.path in ("/", "/ui/clinic/") and c.setup_done:
                from . import readiness

                ctx["ready"] = readiness.card(request)
    except Exception:  # noqa: BLE001 — the menu must never take a page down
        import logging

        logging.getLogger("trio").exception("menu")
    return ctx
