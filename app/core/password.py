from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render

from .models import UserFlag


@login_required
def change(request):
    first = UserFlag.objects.filter(user=request.user, must_change_password=True).exists()
    form = PasswordChangeForm(request.user, request.POST or None)
    for f in form.fields.values():
        f.widget.attrs.update(style="width:100%")
    if request.method == "POST" and form.is_valid():
        u = form.save()
        update_session_auth_hash(request, u)
        UserFlag.objects.filter(user=u).update(must_change_password=False)
        messages.success(request, "تم تغيير كلمة السر")
        return redirect("/")
    return render(request, "core/password.html", dict(form=form, first=first, hide_nav=first))
