"""«دليل البرنامج» inside the program (1.21): the guide of this shop's business — read it here or download the PDF."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render

from core import guide as G
from core import menu
from core.models import CompanySettings


def _pdf(key, lang="ar"):
    names = ([f"guide-{key}-{lang}.pdf"] if lang in ("en", "fr") else []) + [f"guide-{key}.pdf"]
    for name in names:
        for base in (Path(settings.BASE_DIR) / "static" / "ui" / "guides", Path(settings.BASE_DIR) / "staticfiles" / "ui" / "guides"):
            p = base / name
            if p.exists():
                return p
    return None


@login_required
def page(request):
    s = CompanySettings.get()
    key = G.key_for(s)
    lang = getattr(request, "trio_lang", "ar")
    title = G.tr(G.TITLES[key], lang)
    if request.GET.get("pdf"):
        p = _pdf(key, lang)
        if not p:
            raise Http404
        name = f"Trio guide - {title}.pdf" if lang == "en" else (f"Guide Trio - {title}.pdf" if lang == "fr" else f"دليل Trio - {title}.pdf")
        return FileResponse(open(p, "rb"), as_attachment=request.GET.get("pdf") == "download", filename=name.replace("/", "-"),
                            content_type="application/pdf")
    secs = G.localized(G.sections(key, menu.words(s)), lang)
    return render(request, "ui/guide.html", dict(key=key, title=title, secs=secs, has_pdf=bool(_pdf(key, lang))))
