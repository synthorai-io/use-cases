import unittest

from shipping.rates import shipping_cost


class ShippingCost(unittest.TestCase):
    def test_domestic_parcel(self):
        self.assertEqual(shipping_cost(20.00, 2.0, "DE"), 7.30)

    def test_europe_parcel(self):
        self.assertEqual(shipping_cost(20.00, 1.0, "FR"), 12.30)

    def test_unknown_country_ships_at_world_rate(self):
        self.assertEqual(shipping_cost(20.00, 1.0, "BR"), 18.10)

    def test_free_shipping_starts_at_the_threshold(self):
        # An order of exactly 50.00 qualifies.
        self.assertEqual(shipping_cost(50.00, 2.0, "DE"), 0.0)

    def test_free_shipping_is_domestic_only(self):
        self.assertEqual(shipping_cost(80.00, 1.0, "FR"), 12.30)


if __name__ == "__main__":
    unittest.main()
