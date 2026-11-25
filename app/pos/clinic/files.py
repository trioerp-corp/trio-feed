"""Attachments in the patient's file (1.21): results, x-rays, reports — photos from a phone or PDF from the lab.
Kept inside the database (so every backup has them); only real images / PDF are taken, never a program or a page."""
from django.core.exceptions import ValidationError

MAX = 10 * 1024 * 1024
SIGS = [(b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"),
        (b"%PDF-", "application/pdf")]


def sniff(head):
    for sig, ct in SIGS:
        if head.startswith(sig):
            return ct
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def inline(ct):
    return ct.startswith("image/") or ct == "application/pdf"


def attach(patient, upload, *, kind="lab", title="", taken_on=None, visit=None, user=None):
    from .models import PatientFile

    if not upload:
        raise ValidationError("اختر الملف (صورة أو PDF)")
    if upload.size > MAX:
        raise ValidationError("الملف أكبر من 10 ميجابايت — صوّره بجودة أقل أو قسّمه")
    data = upload.read()
    ct = sniff(data[:16])
    if not ct:
        raise ValidationError("نوع الملف غير مقبول — صور (JPG / PNG) أو PDF فقط")
    kinds = dict(PatientFile.KINDS)
    return PatientFile.objects.create(patient=patient, visit=visit, kind=kind if kind in kinds else "other", title=(title or "").strip()[:150],
                                      taken_on=taken_on, filename=(upload.name or "file")[-200:], content_type=ct, size=len(data), data=data,
                                      by=(getattr(user, "first_name", "") or getattr(user, "username", ""))[:100])
