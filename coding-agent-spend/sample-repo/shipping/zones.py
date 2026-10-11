"""Shipping zones and their base rates, in dollars."""

ZONES = {
    "domestic": {"base": 4.90, "per_kg": 1.20},
    "europe": {"base": 9.50, "per_kg": 2.80},
    "world": {"base": 14.00, "per_kg": 4.10},
}

COUNTRY_ZONE = {
    "DE": "domestic",
    "FR": "europe",
    "NL": "europe",
    "ES": "europe",
    "US": "world",
    "JP": "world",
}


def zone_for(country: str) -> str:
    """The zone a country ships to. Unknown countries ship at the world rate."""
    return COUNTRY_ZONE.get(country.upper(), "world")
