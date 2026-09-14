import hashlib
from typing import Any, Callable

from faker import Faker
from pydantic import BaseModel

fake = Faker("id_ID")
Faker.seed(42)

# ---------------------------------------------------------------------------
# Deterministic seeding: same input key = same generated data
# ---------------------------------------------------------------------------

_registry: dict[str, Any] = {}


def _seed_for(key: str) -> int:
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


def _seeded(key: str):
    """Context-like helper: seed Faker deterministically for a key."""
    Faker.seed(_seed_for(key))


# ---------------------------------------------------------------------------
# Pydantic arg models (trust boundary)
# ---------------------------------------------------------------------------

class QueryBorrowerArgs(BaseModel):
    name: str


class QueryLoansArgs(BaseModel):
    borrower_id: str


class QueryBorrowersByCityArgs(BaseModel):
    city: str
    limit: int = 10


class QueryPaymentsArgs(BaseModel):
    loan_id: str


class QueryCollateralArgs(BaseModel):
    loan_id: str


class QueryGuarantorsArgs(BaseModel):
    borrower_id: str


class QueryCollectionRecordsArgs(BaseModel):
    loan_id: str


class QueryTransactionsArgs(BaseModel):
    borrower_id: str
    limit: int = 20


class QueryPortfolioSummaryArgs(BaseModel):
    city: str | None = None


class QueryDelinquencyStatsArgs(BaseModel):
    bucket: str | None = None


# Library bridge arg models

class TabulateDataArgs(BaseModel):
    records: list[dict[str, Any]]
    columns: list[str] | None = None
    sort_by: str | None = None
    ascending: bool = True


class AggregateDataArgs(BaseModel):
    records: list[dict[str, Any]]
    group_by: str | list[str]
    aggregations: dict[str, str]


class PivotDataArgs(BaseModel):
    records: list[dict[str, Any]]
    index: str
    columns: str
    values: str
    aggfunc: str = "sum"


class ComputeStatisticsArgs(BaseModel):
    values: list[float | int]


class ComputeCorrelationArgs(BaseModel):
    x_values: list[float | int]
    y_values: list[float | int]


class AnalyzeNetworkArgs(BaseModel):
    edges: list[list[str]]
    analysis: str = "components"
    source: str | None = None
    target: str | None = None


class FindRelatedEntitiesArgs(BaseModel):
    edges: list[list[str]]
    entity_id: str
    depth: int = 2


# ---------------------------------------------------------------------------
# Domain functions — interconnected via borrower_id / loan_id
# ---------------------------------------------------------------------------

def query_borrower(args_dict: dict[str, Any]) -> dict[str, Any]:
    args = QueryBorrowerArgs.model_validate(args_dict)
    cache_key = f"borrower:{args.name.lower()}"
    if cache_key in _registry:
        return _registry[cache_key]
    _seeded(cache_key)
    bid = fake.uuid4()
    city = fake.city()
    borrower = {
        "borrower_id": bid,
        "name": args.name,
        "address": fake.address(),
        "city": city,
        "phone": fake.phone_number(),
        "email": fake.email(),
        "credit_score": fake.random_int(300, 850),
        "monthly_income": fake.random_int(3_000_000, 50_000_000),
        "employment_status": fake.random_element(["employed", "self_employed", "unemployed"]),
    }
    _registry[cache_key] = borrower
    _registry[f"borrower_by_id:{bid}"] = borrower
    return borrower


def query_loans(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    args = QueryLoansArgs.model_validate(args_dict)
    cache_key = f"loans:{args.borrower_id}"
    if cache_key in _registry:
        return _registry[cache_key]
    _seeded(cache_key)
    count = fake.random_int(1, 5)
    loans = []
    for i in range(count):
        _seeded(f"loan:{args.borrower_id}:{i}")
        loan_id = fake.uuid4()
        loan = {
            "loan_id": loan_id,
            "borrower_id": args.borrower_id,
            "product": fake.random_element(["bolt", "flash", "steady"]),
            "amount": fake.random_int(1_000_000, 100_000_000),
            "tenor_months": fake.random_element([3, 6, 12, 24, 36]),
            "interest_rate": round(fake.pyfloat(min_value=5.0, max_value=25.0), 2),
            "status": fake.random_element(["active", "paid_off", "defaulted", "restructured"]),
            "dpd": fake.random_int(0, 180),
            "disbursed_date": fake.date_between("-2y", "today").isoformat(),
        }
        loans.append(loan)
        _registry[f"loan_by_id:{loan_id}"] = loan
    _registry[cache_key] = loans
    return loans


def query_borrowers_by_city(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    args = QueryBorrowersByCityArgs.model_validate(args_dict)
    _seeded(f"city:{args.city.lower()}")
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


def query_payments(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    args = QueryPaymentsArgs.model_validate(args_dict)
    cache_key = f"payments:{args.loan_id}"
    if cache_key in _registry:
        return _registry[cache_key]
    _seeded(cache_key)
    count = fake.random_int(1, 24)
    payments = []
    for i in range(count):
        _seeded(f"payment:{args.loan_id}:{i}")
        payments.append({
            "payment_id": fake.uuid4(),
            "loan_id": args.loan_id,
            "amount": fake.random_int(100_000, 10_000_000),
            "payment_date": fake.date_between("-2y", "today").isoformat(),
            "method": fake.random_element(["transfer", "auto_debit", "cash", "mobile_payment"]),
            "status": fake.random_element(["completed", "pending", "failed", "reversed"]),
            "late_fee": fake.random_int(0, 500_000),
        })
    _registry[cache_key] = payments
    return payments


def query_collateral(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    args = QueryCollateralArgs.model_validate(args_dict)
    cache_key = f"collateral:{args.loan_id}"
    if cache_key in _registry:
        return _registry[cache_key]
    _seeded(cache_key)
    count = fake.random_int(0, 3)
    items = []
    for i in range(count):
        _seeded(f"collateral:{args.loan_id}:{i}")
        items.append({
            "collateral_id": fake.uuid4(),
            "loan_id": args.loan_id,
            "type": fake.random_element(["vehicle", "property", "equipment", "inventory", "receivables"]),
            "description": fake.sentence(nb_words=6),
            "appraised_value": fake.random_int(5_000_000, 500_000_000),
            "appraisal_date": fake.date_between("-1y", "today").isoformat(),
            "status": fake.random_element(["active", "released", "seized"]),
        })
    _registry[cache_key] = items
    return items


def query_guarantors(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    args = QueryGuarantorsArgs.model_validate(args_dict)
    cache_key = f"guarantors:{args.borrower_id}"
    if cache_key in _registry:
        return _registry[cache_key]
    _seeded(cache_key)
    count = fake.random_int(0, 2)
    guarantors = []
    for i in range(count):
        _seeded(f"guarantor:{args.borrower_id}:{i}")
        guarantors.append({
            "guarantor_id": fake.uuid4(),
            "borrower_id": args.borrower_id,
            "name": fake.name(),
            "relationship": fake.random_element(["spouse", "parent", "sibling", "business_partner"]),
            "phone": fake.phone_number(),
            "monthly_income": fake.random_int(3_000_000, 50_000_000),
            "guarantee_amount": fake.random_int(1_000_000, 100_000_000),
        })
    _registry[cache_key] = guarantors
    return guarantors


def query_collection_records(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    args = QueryCollectionRecordsArgs.model_validate(args_dict)
    cache_key = f"collections:{args.loan_id}"
    if cache_key in _registry:
        return _registry[cache_key]
    _seeded(cache_key)
    count = fake.random_int(0, 10)
    records = []
    for i in range(count):
        _seeded(f"collection:{args.loan_id}:{i}")
        records.append({
            "record_id": fake.uuid4(),
            "loan_id": args.loan_id,
            "action_date": fake.date_between("-1y", "today").isoformat(),
            "action_type": fake.random_element([
                "phone_call", "sms", "email", "field_visit", "legal_notice", "restructure_offer"
            ]),
            "outcome": fake.random_element([
                "promise_to_pay", "no_answer", "refused", "partial_payment", "paid_in_full", "escalated"
            ]),
            "agent": fake.name(),
            "notes": fake.sentence(nb_words=10),
            "next_action_date": fake.date_between("today", "+30d").isoformat(),
        })
    _registry[cache_key] = records
    return records


def query_transactions(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    args = QueryTransactionsArgs.model_validate(args_dict)
    cache_key = f"transactions:{args.borrower_id}"
    if cache_key in _registry:
        return _registry[cache_key]
    _seeded(cache_key)
    transactions = []
    for i in range(args.limit):
        _seeded(f"transaction:{args.borrower_id}:{i}")
        transactions.append({
            "transaction_id": fake.uuid4(),
            "borrower_id": args.borrower_id,
            "date": fake.date_between("-6m", "today").isoformat(),
            "type": fake.random_element([
                "disbursement", "repayment", "fee", "penalty", "refund", "adjustment"
            ]),
            "amount": fake.random_int(50_000, 50_000_000),
            "channel": fake.random_element(["bank_transfer", "mobile", "cash", "auto_debit"]),
            "reference": fake.bothify("TXN-########"),
            "loan_id": fake.uuid4(),
        })
    _registry[cache_key] = transactions
    return transactions


def query_portfolio_summary(args_dict: dict[str, Any]) -> dict[str, Any]:
    args = QueryPortfolioSummaryArgs.model_validate(args_dict)
    key = f"portfolio:{(args.city or 'all').lower()}"
    _seeded(key)
    return {
        "scope": args.city or "all",
        "total_borrowers": fake.random_int(100, 10_000),
        "total_loans": fake.random_int(200, 20_000),
        "total_outstanding": fake.random_int(1_000_000_000, 100_000_000_000),
        "total_disbursed": fake.random_int(2_000_000_000, 200_000_000_000),
        "avg_loan_amount": fake.random_int(5_000_000, 50_000_000),
        "avg_interest_rate": round(fake.pyfloat(min_value=8.0, max_value=20.0), 2),
        "npl_ratio": round(fake.pyfloat(min_value=1.0, max_value=15.0), 2),
        "current_ratio": round(fake.pyfloat(min_value=60.0, max_value=95.0), 2),
        "dpd_30_ratio": round(fake.pyfloat(min_value=2.0, max_value=15.0), 2),
        "dpd_60_ratio": round(fake.pyfloat(min_value=1.0, max_value=10.0), 2),
        "dpd_90_ratio": round(fake.pyfloat(min_value=0.5, max_value=8.0), 2),
    }


def query_delinquency_stats(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    args = QueryDelinquencyStatsArgs.model_validate(args_dict)
    _seeded(f"delinquency:{args.bucket or 'all'}")
    buckets = ["current", "1-30", "31-60", "61-90", "91-120", "120+"]
    if args.bucket and args.bucket in buckets:
        buckets = [args.bucket]
    stats = []
    for b in buckets:
        _seeded(f"delinquency_bucket:{b}")
        stats.append({
            "bucket": b,
            "loan_count": fake.random_int(10, 5000),
            "total_outstanding": fake.random_int(100_000_000, 10_000_000_000),
            "avg_dpd": fake.random_int(0, 180),
            "recovery_rate": round(fake.pyfloat(min_value=10.0, max_value=95.0), 2),
        })
    return stats


# ---------------------------------------------------------------------------
# Library bridge functions — wrap pandas/networkx/numpy for Monty code
# ---------------------------------------------------------------------------

def tabulate_data(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Sort/filter/select columns from records using pandas."""
    import pandas as pd
    args = TabulateDataArgs.model_validate(args_dict)
    df = pd.DataFrame(args.records)
    if args.columns:
        df = df[[c for c in args.columns if c in df.columns]]
    if args.sort_by and args.sort_by in df.columns:
        df = df.sort_values(args.sort_by, ascending=args.ascending)
    return df.to_dict(orient="records")


def aggregate_data(args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Group-by aggregation using pandas. aggfunc: sum/mean/count/min/max."""
    import pandas as pd
    args = AggregateDataArgs.model_validate(args_dict)
    df = pd.DataFrame(args.records)
    group_cols = [args.group_by] if isinstance(args.group_by, str) else args.group_by
    agg_map = {}
    for col, func in args.aggregations.items():
        if col in df.columns:
            agg_map[col] = func
    result = df.groupby(group_cols).agg(agg_map).reset_index()
    return result.to_dict(orient="records")


def pivot_data(args_dict: dict[str, Any]) -> dict[str, Any]:
    """Pivot table using pandas. Returns {columns, index, values}."""
    import pandas as pd
    args = PivotDataArgs.model_validate(args_dict)
    df = pd.DataFrame(args.records)
    pivot = pd.pivot_table(df, index=args.index, columns=args.columns,
                           values=args.values, aggfunc=args.aggfunc, fill_value=0)
    return {
        "columns": list(pivot.columns),
        "index": list(pivot.index),
        "values": pivot.values.tolist(),
    }


def compute_statistics(args_dict: dict[str, Any]) -> dict[str, float]:
    """Descriptive statistics using numpy."""
    import numpy as np
    args = ComputeStatisticsArgs.model_validate(args_dict)
    arr = np.array(args.values, dtype=float)
    return {
        "count": len(arr),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "sum": float(np.sum(arr)),
    }


def compute_correlation(args_dict: dict[str, Any]) -> dict[str, float]:
    """Pearson and Spearman correlation using numpy/scipy."""
    import numpy as np
    args = ComputeCorrelationArgs.model_validate(args_dict)
    x = np.array(args.x_values, dtype=float)
    y = np.array(args.y_values, dtype=float)
    pearson = float(np.corrcoef(x, y)[0, 1])
    # Spearman via rank correlation
    from scipy.stats import spearmanr
    spearman, p_value = spearmanr(x, y)
    return {
        "pearson": pearson,
        "spearman": float(spearman),
        "p_value": float(p_value),
        "n": len(x),
    }


def analyze_network(args_dict: dict[str, Any]) -> dict[str, Any]:
    """Graph analysis using networkx. analysis: components/centrality/shortest_path/degree."""
    import networkx as nx
    args = AnalyzeNetworkArgs.model_validate(args_dict)
    G = nx.Graph()
    G.add_edges_from(args.edges)

    if args.analysis == "components":
        components = list(nx.connected_components(G))
        return {
            "num_components": len(components),
            "components": [sorted(list(c)) for c in components],
            "largest_size": max(len(c) for c in components) if components else 0,
        }
    elif args.analysis == "centrality":
        degree = nx.degree_centrality(G)
        betweenness = nx.betweenness_centrality(G)
        return {
            "degree_centrality": {k: round(v, 4) for k, v in sorted(degree.items(), key=lambda x: -x[1])[:20]},
            "betweenness_centrality": {k: round(v, 4) for k, v in sorted(betweenness.items(), key=lambda x: -x[1])[:20]},
        }
    elif args.analysis == "shortest_path":
        if not args.source or not args.target:
            return {"error": "source and target required for shortest_path"}
        try:
            path = nx.shortest_path(G, args.source, args.target)
            return {"path": path, "length": len(path) - 1}
        except nx.NetworkXNoPath:
            return {"path": None, "length": -1, "error": "no path exists"}
    elif args.analysis == "degree":
        degrees = dict(G.degree())
        return {
            "degrees": {k: v for k, v in sorted(degrees.items(), key=lambda x: -x[1])[:20]},
            "avg_degree": round(sum(degrees.values()) / len(degrees), 2) if degrees else 0,
        }
    return {"error": f"unknown analysis: {args.analysis}"}


def find_related_entities(args_dict: dict[str, Any]) -> dict[str, Any]:
    """BFS from entity_id up to depth. Returns connected entities by distance."""
    import networkx as nx
    args = FindRelatedEntitiesArgs.model_validate(args_dict)
    G = nx.Graph()
    G.add_edges_from(args.edges)
    if args.entity_id not in G:
        return {"entity_id": args.entity_id, "related": {}, "error": "entity not in graph"}
    lengths = nx.single_source_shortest_path_length(G, args.entity_id, cutoff=args.depth)
    by_depth: dict[int, list[str]] = {}
    for node, dist in lengths.items():
        if node != args.entity_id:
            by_depth.setdefault(dist, []).append(node)
    return {"entity_id": args.entity_id, "related": by_depth, "total": sum(len(v) for v in by_depth.values())}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

HOST_FUNCTIONS: dict[str, Callable] = {
    # Core domain
    "query_borrower": query_borrower,
    "query_loans": query_loans,
    "query_borrowers_by_city": query_borrowers_by_city,
    "query_payments": query_payments,
    "query_collateral": query_collateral,
    "query_guarantors": query_guarantors,
    "query_collection_records": query_collection_records,
    "query_transactions": query_transactions,
    # Aggregations
    "query_portfolio_summary": query_portfolio_summary,
    "query_delinquency_stats": query_delinquency_stats,
    # Library bridges (pandas)
    "tabulate_data": tabulate_data,
    "aggregate_data": aggregate_data,
    "pivot_data": pivot_data,
    # Library bridges (numpy/scipy)
    "compute_statistics": compute_statistics,
    "compute_correlation": compute_correlation,
    # Library bridges (networkx)
    "analyze_network": analyze_network,
    "find_related_entities": find_related_entities,
}

HOST_FUNCTION_DESCRIPTIONS: dict[str, str] = {
    # Core domain
    "query_borrower": "query_borrower({'name': str}) -> dict: borrower_id, name, address, city, phone, email, credit_score, monthly_income, employment_status",
    "query_loans": "query_loans({'borrower_id': str}) -> list of loans: loan_id, borrower_id, product, amount, tenor_months, interest_rate, status, dpd, disbursed_date",
    "query_borrowers_by_city": "query_borrowers_by_city({'city': str, 'limit': int=10}) -> list: borrower_id, name, city, credit_score, monthly_income, total_loans, total_outstanding",
    "query_payments": "query_payments({'loan_id': str}) -> list: payment_id, loan_id, amount, payment_date, method, status, late_fee",
    "query_collateral": "query_collateral({'loan_id': str}) -> list: collateral_id, loan_id, type, description, appraised_value, appraisal_date, status",
    "query_guarantors": "query_guarantors({'borrower_id': str}) -> list: guarantor_id, borrower_id, name, relationship, phone, monthly_income, guarantee_amount",
    "query_collection_records": "query_collection_records({'loan_id': str}) -> list: record_id, loan_id, action_date, action_type, outcome, agent, notes, next_action_date",
    "query_transactions": "query_transactions({'borrower_id': str, 'limit': int=20}) -> list: transaction_id, borrower_id, date, type, amount, channel, reference, loan_id",
    # Aggregations
    "query_portfolio_summary": "query_portfolio_summary({'city': str|None}) -> dict: total_borrowers, total_loans, total_outstanding, avg_loan_amount, npl_ratio, dpd buckets",
    "query_delinquency_stats": "query_delinquency_stats({'bucket': str|None}) -> list by DPD bucket: loan_count, total_outstanding, avg_dpd, recovery_rate. Buckets: current, 1-30, 31-60, 61-90, 91-120, 120+",
    # Library bridges
    "tabulate_data": "tabulate_data({'records': list[dict], 'columns': list[str]|None, 'sort_by': str|None, 'ascending': bool=True}) -> sorted/filtered records via pandas",
    "aggregate_data": "aggregate_data({'records': list[dict], 'group_by': str|list[str], 'aggregations': {'col': 'sum|mean|count|min|max'}}) -> grouped results via pandas",
    "pivot_data": "pivot_data({'records': list[dict], 'index': str, 'columns': str, 'values': str, 'aggfunc': str='sum'}) -> {columns, index, values} via pandas",
    "compute_statistics": "compute_statistics({'values': list[float]}) -> dict: count, mean, median, std, min, max, p25, p75, p90, sum via numpy",
    "compute_correlation": "compute_correlation({'x_values': list[float], 'y_values': list[float]}) -> dict: pearson, spearman, p_value, n",
    "analyze_network": "analyze_network({'edges': list[[str,str]], 'analysis': 'components|centrality|shortest_path|degree', 'source': str|None, 'target': str|None}) -> graph analysis via networkx",
    "find_related_entities": "find_related_entities({'edges': list[[str,str]], 'entity_id': str, 'depth': int=2}) -> BFS related entities by distance via networkx",
}


def build_external_lookup(allowlist: list[str] | None = None) -> dict[str, Callable]:
    if allowlist is None:
        return dict(HOST_FUNCTIONS)
    return {k: v for k, v in HOST_FUNCTIONS.items() if k in allowlist}
