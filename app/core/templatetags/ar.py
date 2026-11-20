"""Arabic number agreement: «صنف واحد»، «صنفان»، «3 أصناف»، «11 صنفًا»، «100 صنف» — {{ n|count:"صنف" }}."""
from django import template

register = template.Library()

# singular: (dual, plural, accusative singular for 11–99, feminine?)
FORMS = {
    "صنف": ("صنفان", "أصناف", "صنفًا", False), "حجم": ("حجمان", "أحجام", "حجمًا", False), "منتج": ("منتجان", "منتجات", "منتجًا", False),
    "تصنيف": ("تصنيفان", "تصنيفات", "تصنيفًا", False), "جهاز": ("جهازان", "أجهزة", "جهازًا", False), "مورد": ("موردان", "موردين", "موردًا", False),
    "عميل": ("عميلان", "عملاء", "عميلًا", False), "سطر": ("سطران", "أسطر", "سطرًا", False), "خطأ": ("خطآن", "أخطاء", "خطأً", False),
    "ملصق": ("ملصقان", "ملصقات", "ملصقًا", False), "قسم": ("قسمان", "أقسام", "قسمًا", False), "طلب": ("طلبان", "طلبات", "طلبًا", False),
    "فاتورة": ("فاتورتان", "فواتير", "فاتورة", True), "ترابيزة": ("ترابيزتان", "ترابيزات", "ترابيزة", True),
    "وردية": ("ورديتان", "ورديات", "وردية", True), "إضافة": ("إضافتان", "إضافات", "إضافة", True), "خامة": ("خامتان", "خامات", "خامة", True),
}


def count_ar(n, word):
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return f"{n} {word}"
    dual, plural, acc, fem = FORMS.get(word, (None, None, None, False))
    if dual is None:
        return f"{n} {word}"
    if n == 1:
        return f"{word} {'واحدة' if fem else 'واحد'}"
    if n == 2:
        return dual
    m = n % 100
    if 3 <= m <= 10:
        return f"{n} {plural}"
    if 11 <= m <= 99:
        return f"{n} {acc}"
    return f"{n} {word}"  # 0, 100, 101, 102, 1000…


@register.filter(name="count")
def count(n, word):
    return count_ar(n, word)
