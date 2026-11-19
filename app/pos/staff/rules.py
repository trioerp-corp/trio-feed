"""Egyptian payroll and tax figures, by year (1.21). ONE place to change when the law or the yearly limits change.

Sources checked 17/11/2026:
- Salary (كسب العمل) brackets + personal exemption 20,000: Law 7/2024 (Income Tax Law 91/2005 as amended); high incomes lose the
  lower brackets (600k / 700k / 800k / 900k / 1.2M).
- Social insurance, Law 148/2019: employee 11%, employer 18.75% of the insurable wage (to 31/12/2026); from 1/1/2027 employee
  11.5%, employer 19.25%. Insurable wage 2026: 2,700 – 16,700 a month (National Social Insurance Authority, effective 1/1/2026).
- Martyrs fund (Law 4/2021): 5 / 10,000 of the salary every month.
- Simplified regime (Law 6/2025): turnover up to 20 million a year, a rate on the whole turnover by band.
- VAT 14% (Law 67/2016); a doctor's professional health services are exempt.
Everything the program prints from these is a DRAFT for the shop's accountant to check before filing.
"""
from decimal import ROUND_HALF_UP, Decimal

D = Decimal

# (upper limit of the band, rate %) — the last band has no limit
BRACKETS = [(D(40000), D(0)), (D(55000), D(10)), (D(70000), D(15)), (D(200000), D(20)), (D(400000), D(22.5)), (D(1200000), D(25)), (None, D(27.5))]
# income above → the bands under this limit are taxed at this single rate instead (the «elimination» of the lower bands)
ELIMINATION = [(D(1200000), D(1200000), D(25)), (D(900000), D(400000), D(22.5)), (D(800000), D(200000), D(20)), (D(700000), D(70000), D(15)),
               (D(600000), D(55000), D(10))]

YEARS = {
    2026: dict(exemption=D(20000), ins_min=D(2700), ins_max=D(16700), emp_ins=D(11), er_ins=D("18.75"), martyrs=D("0.05")),
    2027: dict(exemption=D(20000), ins_min=D(2700), ins_max=D(16700), emp_ins=D("11.5"), er_ins=D("19.25"), martyrs=D("0.05"),
               note="حدود أجر الاشتراك 2027 لم تُعلن وقت الإصدار — تأكد منها"),
}

SIMPLIFIED = [(D(500000), D("0.4")), (D(2000000), D("0.5")), (D(3000000), D("0.75")), (D(10000000), D(1)), (D(20000000), D("1.5"))]
SIMPLIFIED_LIMIT = D(20000000)
COMPANY_RATE = D("22.5")


def year(y):
    """The figures of a year (the nearest known year before it when it is not listed yet)."""
    known = sorted(YEARS)
    best = max([k for k in known if k <= y], default=known[0])
    return dict(YEARS[best], year=best, exact=best == y)


def q2(x):
    return D(x).quantize(D("0.01"), ROUND_HALF_UP)


def progressive(income):
    """Annual tax on net taxable income, with the lower bands taken away from high incomes."""
    income = max(D(income), D(0))
    for above, limit, rate in ELIMINATION:
        if income > above:
            tax = min(income, limit) * rate / 100
            low = limit
            for top, r in BRACKETS:
                if top is not None and top <= low:
                    continue
                part = (min(income, top) if top is not None else income) - low
                if part > 0:
                    tax += part * r / 100
                low = top if top is not None else income
                if top is None or income <= top:
                    break
            return q2(tax)
    tax, low = D(0), D(0)
    for top, r in BRACKETS:
        hi = income if top is None else min(income, top)
        if hi > low:
            tax += (hi - low) * r / 100
        if top is None or income <= top:
            break
        low = top
    return q2(tax)


def salary_tax_month(gross_month, emp_ins_month, y):
    """The month's salary tax: (gross − employee insurance) × 12 − personal exemption → yearly tax ÷ 12."""
    f = year(y)
    annual = (D(gross_month) - D(emp_ins_month)) * 12 - f["exemption"]
    return q2(progressive(annual) / 12)


def insurance(gross_month, y, insurable=None):
    f = year(y)
    base = D(insurable) if insurable not in (None, "") else D(gross_month)
    base = min(max(base, f["ins_min"]), f["ins_max"])
    return dict(insurable=q2(base), emp=q2(base * f["emp_ins"] / 100), er=q2(base * f["er_ins"] / 100))


def martyrs(gross_month, y):
    return q2(D(gross_month) * year(y)["martyrs"] / 100)


def simplified_rate(turnover):
    t = D(turnover)
    for top, rate in SIMPLIFIED:
        if t < top or (top == SIMPLIFIED_LIMIT and t <= top):
            return rate
    return None  # above 20 million: not eligible
