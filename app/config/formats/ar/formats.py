# Numbers as Egyptian shops write them (1.23 QA): 1,250.50 — Django's Arabic locale writes 1.250,50, so a weight of
# 6.75 kg showed as «6,750» (read as six thousand) and margins as «13,6%».
from django.conf.locale.ar.formats import *  # noqa: F401,F403

DECIMAL_SEPARATOR = "."
THOUSAND_SEPARATOR = ","
NUMBER_GROUPING = 3
