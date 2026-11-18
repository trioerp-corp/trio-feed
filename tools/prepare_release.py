"""Runs in GitHub Actions when the phone panel uploads an update zip to incoming/<name>.zip (1.22).

    python tools/prepare_release.py incoming/<name>.zip [base Trio-online.zip] [out Trio-online.zip]

- the clients' zip: the update without the sealed Trio License (`_trio_license/`) → updates/trio-<v>.zip + .json {v, sha, n, notes}
  (the phone then signs and publishes it with the update key, exactly like a release prepared on the computer);
- the full download package: the last Trio-online.zip with the new program files laid over it (same rule as Trio License);
- incoming/<name>.done (the version) or incoming/<name>.err (why) so the phone panel can tell what happened.
"""
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path


def fail(name, msg):
    Path(f"incoming/{name}.err").write_text(msg, encoding="utf-8")
    print("ERROR:", msg)
    sys.exit(0)


def main():
    src = Path(sys.argv[1])
    name = src.stem
    try:
        zin = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        fail(name, "الملف ليس ملف zip سليمًا")
    names = zin.namelist()
    if any(n.startswith("Trio/") and n.endswith("Trio.exe") for n in names):
        fail(name, "هذا ملف التحميل الكامل وليس ملف التحديث — ارفع «Trio - تحديث … (كامل).zip»")
    if "core/version.py" not in names:
        fail(name, "هذا ليس ملف تحديث Trio (لا يحتوي على core/version.py)")
    m = re.search(r'VERSION\s*=\s*"([0-9.]+)"', zin.read("core/version.py").decode("utf-8", "ignore"))
    if not m:
        fail(name, "لم أجد رقم الإصدار داخل الملف")
    ver = m.group(1)
    if Path(f"updates/trio-{ver}.json").exists():
        fail(name, f"الإصدار {ver} مرفوع من قبل — كل تعديل يحتاج رقم إصدار جديد")
    notes = ""
    for n in names:
        if "/" not in n and n.startswith("اقرأني"):
            lines = zin.read(n).decode("utf-8-sig", "ignore").splitlines()[2:]
            notes = "\n".join(l for l in lines if l.strip() and not l.startswith(("النشر:", "Trio License"))).strip()[:600]
    notes = notes or "تحسينات وإصلاحات"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i in zin.infolist():
            if not i.filename.startswith("_trio_license/"):
                z.writestr(i, zin.read(i))
    data = out.getvalue()
    Path("updates").mkdir(exist_ok=True)
    Path(f"updates/trio-{ver}.zip").write_bytes(data)
    Path(f"updates/trio-{ver}.json").write_text(json.dumps(dict(v=ver, sha=hashlib.sha256(data).hexdigest(), n=len(data), notes=notes, src="phone"),
                                                          ensure_ascii=False, indent=1), encoding="utf-8")
    # the full download package (when the last one could be fetched)
    if len(sys.argv) > 3 and Path(sys.argv[2]).exists():
        base = zipfile.ZipFile(sys.argv[2])
        pre = next((n.split("/")[0] + "/" for n in base.namelist() if n.count("/") == 1 and n.endswith("/Trio.exe")), "Trio/")
        repl = {pre + i.filename: i for i in zin.infolist() if not i.filename.endswith("/") and not i.filename.startswith("_trio_license/")
                and not ("/" not in i.filename and i.filename.endswith(".txt"))}
        with zipfile.ZipFile(sys.argv[3], "w", zipfile.ZIP_DEFLATED) as z:
            for i in base.infolist():
                if i.filename in repl:
                    z.writestr(i.filename, zin.read(repl.pop(i.filename)))
                else:
                    z.writestr(i, base.read(i))
            for n, i in repl.items():
                z.writestr(n, zin.read(i))
        print("online package:", sys.argv[3])
    Path(f"incoming/{name}.done").write_text(ver, encoding="utf-8")
    print("release", ver, len(data), "bytes")
    Path("RELEASE_VERSION").write_text(ver)
    Path("RELEASE_NOTES").write_text(notes, encoding="utf-8")


if __name__ == "__main__":
    main()
