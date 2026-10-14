"""Unit-of-measure normalisation (МКЕИ codes from /v3/refs/ref_units).

Every unit maps to (base_unit, factor): quantity_base = quantity * factor, price_base = price / factor.
Prices are only ever compared within the same base unit. Packaging units (pack, set, box…) have no physical
conversion and stay as their own base — comparing "pack" to "piece" would be meaningless.
"""
from __future__ import annotations

# code -> (base_unit, factor)
UNIT_MAP: dict[str, tuple[str, float]] = {
    # mass -> kg
    "166": ("kg", 1.0), "163": ("kg", 0.001), "161": ("kg", 1e-6), "168": ("kg", 1000.0), "170": ("kg", 1e6),
    "206": ("kg", 100.0),  # центнер
    # volume -> L
    "112": ("L", 1.0), "111": ("L", 0.001), "113": ("L", 1000.0), "122": ("L", 100.0), "114": ("L", 1e6),
    # length -> m
    "006": ("m", 1.0), "018": ("m", 1.0), "5156": ("m", 1.0), "004": ("m", 0.01), "003": ("m", 0.001),
    "008": ("m", 1000.0), "019": ("m", 1000.0),
    # area -> m2
    "055": ("m2", 1.0), "058": ("m2", 1000.0), "059": ("m2", 10000.0), "061": ("m2", 1e6),
    # pieces
    "796": ("pcs", 1.0), "798": ("pcs", 1000.0), "799": ("pcs", 1e6),
    # energy / power
    "245": ("kWh", 1.0), "246": ("kWh", 1000.0), "214": ("kW", 1.0), "215": ("kW", 1000.0),
    "233": ("Gcal", 1.0),
    # services / works are counted as single units
    "5114": ("service", 1.0), "980": ("work", 1.0),
}


def normalize_unit(code: str | None) -> tuple[str, float]:
    """Return (base_unit, factor). Unknown codes keep their own code as base with factor 1."""
    if code is None or code == "":
        return ("unknown", 1.0)
    code = code.strip()
    # unmapped units are kept apart rather than guessed
    return UNIT_MAP.get(code, (f"u{code}", 1.0))
