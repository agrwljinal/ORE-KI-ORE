"""Customer-contract allocation and delivery-liability calculations.

This module intentionally stays independent of the production and
prescriptive engines.  It converts their daily ROM forecast into a customer
delivery outlook, using the commercial terms supplied for each contract.
"""
from __future__ import annotations

import json
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional


DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "customers.json"
PRODUCTION_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "processed_production.csv"
VALID_PENALTY_TYPES = {"INR_PER_MT", "PERCENT_OF_SHORT_VALUE"}


@dataclass(frozen=True)
class CustomerContract:
    contract_id: str
    customer_name: str
    quantity_contracted_mt: float
    price_offered_per_mt: float
    delivery_deadline: str
    penalty_type: str
    penalty_value: float
    assigned_mine: str
    status: str = "ACTIVE"

    @property
    def deadline(self) -> date:
        return datetime.strptime(self.delivery_deadline, "%Y-%m-%d").date()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric.") from exc
    if result < minimum:
        raise ValueError(f"{field} must be at least {minimum}.")
    return result


def normalise_contract(payload: dict[str, Any], contract_id: Optional[str] = None) -> CustomerContract:
    """Validate the API/data fixture shape and return a typed contract."""
    name = str(payload.get("customer_name") or "").strip()
    mine = str(payload.get("assigned_mine") or "").strip()
    if not name:
        raise ValueError("customer_name is required.")
    if not mine:
        raise ValueError("assigned_mine is required.")
    deadline = str(payload.get("delivery_deadline") or "").strip()
    try:
        datetime.strptime(deadline, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("delivery_deadline must use YYYY-MM-DD.") from exc
    penalty_type = str(payload.get("penalty_type") or "INR_PER_MT").upper()
    if penalty_type not in VALID_PENALTY_TYPES:
        raise ValueError("penalty_type must be INR_PER_MT or PERCENT_OF_SHORT_VALUE.")
    return CustomerContract(
        contract_id=str(contract_id or payload.get("contract_id") or "").strip(),
        customer_name=name,
        quantity_contracted_mt=_number(payload.get("quantity_contracted_mt"), "quantity_contracted_mt", minimum=0.01),
        price_offered_per_mt=_number(payload.get("price_offered_per_mt"), "price_offered_per_mt", minimum=0.0),
        delivery_deadline=deadline,
        penalty_type=penalty_type,
        penalty_value=_number(payload.get("penalty_value", 0), "penalty_value", minimum=0.0),
        assigned_mine=mine,
        status=str(payload.get("status") or "ACTIVE").upper(),
    )


class CustomerContractStore:
    """Demo-safe in-memory customer entity store seeded from JSON.

    Runtime additions deliberately remain in memory, matching the existing
    dashboard's SYSTEM_STATE behaviour.  A production deployment should
    replace this boundary with an authenticated contract-management database.
    """

    def __init__(self, data_path: Path = DEFAULT_DATA_PATH) -> None:
        self.data_path = data_path
        self._customers, self._contracts = self._load_seed_data()
        self._next_number = len(self._contracts) + 1
        self._next_customer_number = len(self._customers) + 1

    def _load_seed_data(self) -> tuple[list[dict[str, Any]], list[CustomerContract]]:
        if not self.data_path.exists():
            return [], []
        payload = json.loads(self.data_path.read_text(encoding="utf-8"))
        # Backward compatibility: the first customer fixture stored contracts
        # directly under `customers`.  The current form uses customers +
        # contracts as two related entities.
        if isinstance(payload, dict) and "contracts" in payload:
            customer_rows = payload.get("customers", [])
            rows = payload.get("contracts", [])
        else:
            customer_rows = []
            rows = payload.get("customers", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("customers.json must contain a customer list.")
        contracts = [normalise_contract(row) for row in rows if isinstance(row, dict)]
        customers = [self._normalise_customer(row) for row in customer_rows if isinstance(row, dict)]
        if not customers:
            names = sorted({contract.customer_name for contract in contracts})
            customers = [
                {"customer_id": f"CUS-{index:03d}", "customer_name": name,
                 "contact_person": "", "contact_email": "", "contact_phone": "", "assigned_mine": "Balaghat"}
                for index, name in enumerate(names, start=1)
            ]
        return customers, contracts

    @staticmethod
    def _normalise_customer(payload: dict[str, Any], customer_id: Optional[str] = None) -> dict[str, str]:
        name = str(payload.get("customer_name") or "").strip()
        mine = str(payload.get("assigned_mine") or "").strip()
        if not name:
            raise ValueError("customer_name is required.")
        if not mine:
            raise ValueError("assigned_mine is required.")
        return {
            "customer_id": str(customer_id or payload.get("customer_id") or "").strip(),
            "customer_name": name,
            "contact_person": str(payload.get("contact_person") or "").strip(),
            "contact_email": str(payload.get("contact_email") or "").strip(),
            "contact_phone": str(payload.get("contact_phone") or "").strip(),
            "assigned_mine": mine,
        }

    def list(self) -> list[CustomerContract]:
        return list(self._contracts)

    def list_customers(self) -> list[dict[str, str]]:
        return list(self._customers)

    def add_customer(self, payload: dict[str, Any]) -> dict[str, str]:
        customer = self._normalise_customer(payload, customer_id=f"CUS-{self._next_customer_number:03d}")
        if any(row["customer_name"].casefold() == customer["customer_name"].casefold() for row in self._customers):
            raise ValueError("A customer with this name already exists.")
        self._customers.append(customer)
        self._next_customer_number += 1
        return customer

    def add(self, payload: dict[str, Any]) -> CustomerContract:
        contract_id = f"CON-{self._next_number:03d}"
        contract = normalise_contract(payload, contract_id=contract_id)
        if not any(row["customer_name"].casefold() == contract.customer_name.casefold() for row in self._customers):
            raise ValueError("Add the customer before creating a contract.")
        self._contracts.append(contract)
        self._next_number += 1
        return contract


def _penalty_for_shortfall(contract: CustomerContract, short_mt: float) -> float:
    if contract.penalty_type == "INR_PER_MT":
        return short_mt * contract.penalty_value
    return short_mt * contract.price_offered_per_mt * (contract.penalty_value / 100.0)


def actual_output_regression(mine_name: str, data_path: Path = PRODUCTION_DATA_PATH) -> Optional[dict[str, Any]]:
    """Fit y = mx + c to recent daily actual ROM data for a mine."""
    if not data_path.exists():
        return None
    rows: list[tuple[str, float]] = []
    with data_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("mine_name") or "").casefold() != mine_name.casefold():
                continue
            try:
                rows.append((str(row.get("date") or ""), float(row.get("actual_rom_tonnes") or 0.0)))
            except (TypeError, ValueError):
                continue
    rows.sort(key=lambda item: item[0])
    rows = rows[-30:]
    if len(rows) < 2:
        return None
    n = len(rows)
    mean_x = (n - 1) / 2.0
    mean_y = sum(value for _, value in rows) / n
    divisor = sum((index - mean_x) ** 2 for index in range(n))
    slope = sum((index - mean_x) * (value - mean_y) for index, (_, value) in enumerate(rows)) / divisor if divisor else 0.0
    intercept = mean_y - slope * mean_x
    return {
        "formula": "y = mx + c", "slope_mt_per_day": round(slope, 4),
        "intercept_mt": round(intercept, 4), "origin_index": n - 1,
        "fitted_today_daily_mt": round(max(0.0, intercept + slope * (n - 1)), 2),
        "sample_count": n, "last_observed_date": rows[-1][0],
        # The client plots these observations on the left of the TODAY marker.
        # Keeping the source values alongside the fitted coefficients also makes
        # the y = mx + c line auditable in the demo.
        "history": [
            {"date": observed_on, "actual_rom_tonnes": round(actual, 2)}
            for observed_on, actual in rows
        ],
        "source": "processed_production.csv actual_rom_tonnes",
    }


def calculate_customer_portfolio(
    contracts: Iterable[CustomerContract],
    predicted_daily_tonnage: float,
    assigned_mine: Optional[str],
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Allocate forecast output by earliest deadline and quantify liability.

    The production model produces a *daily* ROM estimate.  For a selected
    mine, available volume is therefore forecast_daily_output × inclusive days
    to each deadline.  Earlier contracts reserve capacity first.  A shortfall
    exposes both the undelivered contract value and the customer-specific
    penalty.  This is forecast exposure, not an invoice or an accrued charge.
    """
    reference_day = today or date.today()
    mine = (assigned_mine or "Balaghat").strip()
    daily_output = max(0.0, float(predicted_daily_tonnage or 0.0))
    active = [
        contract for contract in contracts
        if contract.status == "ACTIVE" and contract.assigned_mine.casefold() == mine.casefold()
    ]
    active.sort(key=lambda contract: (contract.deadline, contract.contract_id))

    allocated_total = 0.0
    rows: list[dict[str, Any]] = []
    for contract in active:
        days_to_deadline = (contract.deadline - reference_day).days
        forecast_days = max(days_to_deadline + 1, 0)
        # Capacity is cumulative through a deadline.  Earlier commitments
        # reserve that same mine output first, preventing double allocation
        # when two customers have different delivery dates.
        capacity_by_deadline = daily_output * forecast_days
        available = max(0.0, capacity_by_deadline - allocated_total)
        expected_delivery = min(contract.quantity_contracted_mt, available)
        allocated_total += expected_delivery
        short_mt = max(0.0, contract.quantity_contracted_mt - expected_delivery)
        short_value = short_mt * contract.price_offered_per_mt
        penalty = _penalty_for_shortfall(contract, short_mt)
        overdue = contract.deadline < reference_day
        status = "OVERDUE" if overdue else ("AT_RISK" if short_mt else "ON_TRACK")
        rows.append({
            **contract.to_dict(),
            "days_to_deadline": days_to_deadline,
            "forecast_days": forecast_days,
            "expected_delivery_mt": round(expected_delivery, 2),
            "shortfall_mt": round(short_mt, 2),
            "expected_revenue_inr": round(expected_delivery * contract.price_offered_per_mt, 2),
            "contract_value_inr": round(contract.quantity_contracted_mt * contract.price_offered_per_mt, 2),
            "short_value_inr": round(short_value, 2),
            "penalty_liability_inr": round(penalty, 2),
            "delivery_status": status,
        })

    total = lambda key: round(sum(float(row.get(key) or 0.0) for row in rows), 2)
    short_value = total("short_value_inr")
    penalty = total("penalty_liability_inr")
    liability = round(short_value + penalty, 2)
    return {
        "assigned_mine": mine,
        "forecast_daily_output_mt": round(daily_output, 2),
        "as_of_date": reference_day.isoformat(),
        "contracts": rows,
        "active_contract_count": len(rows),
        "quantity_contracted_mt": total("quantity_contracted_mt"),
        "expected_delivery_mt": total("expected_delivery_mt"),
        "shortfall_mt": total("shortfall_mt"),
        "contract_value_inr": total("contract_value_inr"),
        "expected_revenue_inr": total("expected_revenue_inr"),
        "short_value_inr": short_value,
        "penalty_liability_inr": penalty,
        "total_liability_inr": liability,
        "delivery_status": "AT_RISK" if liability else "ON_TRACK",
        "actual_output_regression": actual_output_regression(mine),
        "method_note": (
            "Forecast exposure only: daily predicted ROM is allocated to active contracts "
            "for the selected mine by earliest delivery deadline. Liability equals "
            "undelivered contract value plus the contract-specific short-delivery penalty."
        ),
    }


__all__ = [
    "CustomerContract",
    "CustomerContractStore",
    "VALID_PENALTY_TYPES",
    "calculate_customer_portfolio",
    "actual_output_regression",
    "normalise_contract",
]
