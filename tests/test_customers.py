from datetime import date
import unittest

from modules.customers import calculate_customer_portfolio, normalise_contract


def contract(**overrides):
    payload = {
        "contract_id": "CON-001",
        "customer_name": "Test Customer",
        "quantity_contracted_mt": 150,
        "price_offered_per_mt": 100,
        "delivery_deadline": "2026-09-12",
        "penalty_type": "INR_PER_MT",
        "penalty_value": 10,
        "assigned_mine": "Balaghat",
        "status": "ACTIVE",
    }
    payload.update(overrides)
    return normalise_contract(payload)


class CustomerPortfolioTests(unittest.TestCase):
    def test_fixed_per_mt_penalty_makes_shortfall_a_contract_liability(self):
        portfolio = calculate_customer_portfolio(
            [contract()], predicted_daily_tonnage=100, assigned_mine="Balaghat",
            today=date(2026, 9, 12),
        )

        self.assertEqual(portfolio["expected_delivery_mt"], 100.0)
        self.assertEqual(portfolio["shortfall_mt"], 50.0)
        self.assertEqual(portfolio["short_value_inr"], 5000.0)
        self.assertEqual(portfolio["penalty_liability_inr"], 500.0)
        self.assertEqual(portfolio["total_liability_inr"], 5500.0)

    def test_capacity_is_reserved_for_the_earliest_deadline_first(self):
        urgent = contract(contract_id="CON-001", quantity_contracted_mt=150, delivery_deadline="2026-09-12")
        later = contract(contract_id="CON-002", quantity_contracted_mt=150, delivery_deadline="2026-09-13")
        portfolio = calculate_customer_portfolio(
            [later, urgent], predicted_daily_tonnage=100, assigned_mine="Balaghat",
            today=date(2026, 9, 12),
        )

        first, second = portfolio["contracts"]
        self.assertEqual(first["contract_id"], "CON-001")
        self.assertEqual(first["expected_delivery_mt"], 100.0)
        self.assertEqual(second["expected_delivery_mt"], 100.0)
        self.assertEqual(second["shortfall_mt"], 50.0)

    def test_percent_penalty_uses_only_the_undelivered_contract_value(self):
        percentage = contract(
            quantity_contracted_mt=200,
            penalty_type="PERCENT_OF_SHORT_VALUE",
            penalty_value=10,
        )
        portfolio = calculate_customer_portfolio(
            [percentage], predicted_daily_tonnage=100, assigned_mine="Balaghat",
            today=date(2026, 9, 12),
        )

        self.assertEqual(portfolio["penalty_liability_inr"], 1000.0)


if __name__ == "__main__":
    unittest.main()
