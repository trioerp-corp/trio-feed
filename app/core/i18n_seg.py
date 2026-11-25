"""Arabic segment tokenizer shared by tools/i18n/extract.py and core/i18n_mw.py.

A segment is a maximal run of Arabic words joined by spaces, "/" or "-". Commas, digits, Latin text, tags and
quotes end it, so a translation always replaces a whole run — never a piece of a longer Arabic word."""
import re

_L = "ء-يٮ-ۓۺ-ۿ"          # Arabic letters
_M = "ً-ٰٟـ‌"                     # diacritics, tatweel, ZWNJ
_S = "  "
SEG = re.compile(f"[{_L}][{_L}{_M}]*(?:(?:[{_S}]+|[{_S}]*[/\\-–][{_S}]*)[{_L}][{_L}{_M}]*)*")
HAS_AR = re.compile(f"[{_L}]")


def segments(text):
    return {m.group(0) for m in SEG.finditer(text)}
