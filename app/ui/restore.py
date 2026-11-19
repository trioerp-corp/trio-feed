"""«استرجاع نسخة احتياطية»: the owner picks a backup (this computer / Google Drive) or uploads one, sees what is inside, types
his password and confirms. On a fresh copy (new computer, nothing set up yet) the same page opens from the first screen."""
from django.contrib import messages
from django.contrib.auth import authenticate, logout
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render

from core import backups as BK
from core.models import CompanySettings

MAX_UPLOAD = 500 * 1024 * 1024


def _may(request):
    from pos.branches import is_owner_level

    u = request.user
    if not CompanySettings.get().setup_done:
        from core.devaccess import _on_this_machine

        # a fresh copy brings its data back from its own computer — never from another device on the network
        return "fresh" if _on_this_machine(request) else ""
    if u.is_authenticated and (u.is_superuser or is_owner_level(u)):
        return "owner"
    return ""


def restore_page(request):
    who = _may(request)
    if not who:
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        raise PermissionDenied
    s = CompanySettings.get()
    ctx = dict(who=who, rows=BK.listing() if who == "owner" else [], hide_nav=who == "fresh", s=s)
    if request.method == "POST":
        a = request.POST.get("action")
        try:
            if a == "check":
                k = BK.norm_key(request.POST.get("key", "")) or (s.backup_key if who == "owner" else "")
                f = request.FILES.get("file")
                if f:
                    if f.size > MAX_UPLOAD:
                        raise ValidationError("الملف كبير أوي")
                    blob = f.read()
                    ctx["src"] = f.name
                elif request.POST.get("path") and who == "owner":
                    if not BK.allowed_path(request.POST["path"]):
                        raise PermissionDenied
                    with open(request.POST["path"], "rb") as fh:
                        blob = fh.read()
                    ctx["src"] = request.POST["path"]
                else:
                    raise ValidationError("اختر نسخة من القائمة أو ارفع ملفًا")
                if blob.startswith(BK.MAGIC) and not k:
                    raise ValidationError("هذه النسخة مشفّرة — اكتب مفتاح الاسترجاع (من البطاقة المطبوعة أو من Trio)")
                path, facts = BK.inspect(BK.unpack(blob, k))
                request.session["restore_path"] = path
                ctx.update(facts=facts, step="confirm", same=facts.get("shop") == s.name or who == "fresh")
            elif a == "go":
                path = request.session.get("restore_path")
                if not path:
                    raise ValidationError("افحص النسخة الأول")
                if request.POST.get("sure", "").strip() != "استرجاع":
                    raise ValidationError("اكتب كلمة «استرجاع» للتأكيد")
                if who == "owner" and not authenticate(request, username=request.user.username, password=request.POST.get("password", "")):
                    raise ValidationError("كلمة السر غير صحيحة")
                safety = BK.restore(path)
                request.session.pop("restore_path", None)
                logout(request)
                messages.success(request, "تم استرجاع النسخة ✔ — ادخل بحسابات هذه النسخة." + (f" حُفظت نسخة من بياناتك قبل الاسترجاع: {safety.name}" if safety else "")
                                 + (" الرخصة مرتبطة بالجهاز القديم: تواصل مع Trio من أجل رخصة هذا الجهاز." if who == "fresh" else ""))
                return redirect("/login/")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    return render(request, "ui/restore.html", ctx)
