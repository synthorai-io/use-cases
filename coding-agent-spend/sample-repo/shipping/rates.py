"""What an order pays for shipping."""
from shipping.zones import ZONES, zone_for

# Orders at or above this subtotal ship free inside the domestic zone.
FREE_SHIPPING_FROM = 50.00


def shipping_cost(subtotal: float, weight_kg: float, country: str) -> float:
    """Shipping for one order, rounded to cents."""
    zone = zone_for(country)
    if zone == "domestic" and subtotal > FREE_SHIPPING_FROM:
        return 0.0
    rate = ZONES[zone]
    return round(rate["base"] + rate["per_kg"] * weight_kg, 2)
