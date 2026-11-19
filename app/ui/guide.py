"""«دليل البرنامج» inside the program (1.21): the guide of this shop's business — read it here or download the PDF."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render

from core import guide as G
from core import menu
from core.models import CompanySettings


def _pdf(key):
    for base in (Path(settings.BASE_DIR) / "static" / "ui" / "guides", Path(settings.BASE_DIR) / "staticfiles" / "ui" / "guides"):
        p = base / f"guide-{key}.pdf"
        if p.exists():
            return p
    return None


@login_required
def page(request):
    s = CompanySettings.get()
    key = G.key_for(s)
    if request.GET.get("pdf"):
        p = _pdf(key)
        if not p:
            raise Http404
        return FileResponse(open(p, "rb"), as_attachment=request.GET.get("pdf") == "download", filename=f"دليل Trio - {G.TITLES[key]}.pdf",
                            content_type="application/pdf")
    return render(request, "ui/guide.html", dict(key=key, title=G.TITLES[key], secs=G.sections(key, menu.words(s)), has_pdf=bool(_pdf(key))))
