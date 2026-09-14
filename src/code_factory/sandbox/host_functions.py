from typing import Any, Callable

from faker import Faker
from pydantic import BaseModel

fake = Faker("id_ID")
Faker.seed(42)


class QueryBorrowerArgs(BaseModel):
    name: str


class QueryLoansArgs(BaseModel):
    borrower_id: str


class QueryBorrowersByCityArgs(BaseModel):
    city: str
    limit: int = 10


def query_borrower(args_dict: dict[str, Any]) -> dict[str, Any]:
    args = QueryBorrowerArgs.model_validate(args_dict)
    bid = fake.uuid4()
    return {
        "id": bid,
        "borrower_id": bid,
        "name": args.name,
        "address": fake.address(),
        "city": fake.city(),
        "phone": fake.phone_number(),
        "email": fake.email(),
        "credit_score": fake.random_int(300, 850),
        "monthly_income": fake.random_int(3_000_000, 50_000_000),
        "employment_status": fake.random_element(["employed", "self_employed", "unemployed"]),
    }


def query_loans(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    args = QueryLoansArgs.model_validate(args_dict)
    count = fake.random_int(1, 5)
    loans = []
    for _ in range(count):
        loans.append({
            "loan_id": fake.uuid4(),
            "borrower_id": args.borrower_id,
            "product": fake.random_element(["bolt", "flash", "steady"]),
            "amount": fake.random_int(1_000_000, 100_000_000),
            "tenor_months": fake.random_element([3, 6, 12, 24, 36]),
            "interest_rate": round(fake.pyfloat(min_value=5.0, max_value=25.0), 2),
            "status": fake.random_element(["active", "paid_off", "defaulted", "restructured"]),
            "dpd": fake.random_int(0, 180),
            "disbursed_date": fake.date_between("-2y", "today").isoformat(),
        })
    return loans


def query_borrowers_by_city(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    args = QueryBorrowersByCityArgs.model_validate(args_dict)
    return [
        {
            "borrower_id": fake.uuid4(),
            "name": fake.name(),
            "city": args.city,
            "credit_score": fake.random_int(300, 850),
            "monthly_income": fake.random_int(3_000_000, 50_000_000),
            "total_loans": fake.random_int(0, 5),
            "total_outstanding": fake.random_int(0, 200_000_000),
        }
        for _ in range(args.limit)
    ]


HOST_FUNCTIONS: dict[str, Callable] = {
    "query_borrower": query_borrower,
    "query_loans": query_loans,
    "query_borrowers_by_city": query_borrowers_by_city,
}

HOST_FUNCTION_DESCRIPTIONS: dict[str, str] = {
    "query_borrower": "query_borrower({'name': str}) -> dict with keys: id, borrower_id, name, address, city, phone, email, credit_score, monthly_income, employment_status",
    "query_loans": "query_loans({'borrower_id': str}) -> list of loan records (amount, tenor, interest, status, dpd, etc)",
    "query_borrowers_by_city": "query_borrowers_by_city({'city': str, 'limit': int}) -> list of borrower summaries in that city",
}


def build_external_lookup(allowlist: list[str] | None = None) -> dict[str, Callable]:
    if allowlist is None:
        return dict(HOST_FUNCTIONS)
    return {k: v for k, v in HOST_FUNCTIONS.items() if k in allowlist}
