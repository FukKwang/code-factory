"""Export training triples + vault examples + orchestrator tool-call examples to chat-format JSONL."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_factory.agents.coder import MONTY_LIMITATIONS
from code_factory.sandbox.host_functions import HOST_FUNCTION_DESCRIPTIONS

VAULT_DIR = Path("/home/kwang/Documents/dev/code-factory-repo/tickets")
TRIPLES_FILE = Path(__file__).parent.parent / "training_data" / "triples.json"
OUT_FILE = Path(__file__).parent.parent / "training_data" / "finetune.jsonl"

# --- Coder system prompt (unchanged) ---
CODER_SYSTEM = f"""{MONTY_LIMITATIONS}

You will be given:
1. A specification describing what the program should do
2. Available host functions with their signatures
3. Input parameters the program receives via the `inputs` dict
4. Test assertions the code must pass

Write ONLY the Python code. No markdown fences, no explanations."""

# --- Orchestrator system prompt (matches orchestrator.py) ---
FN_NAMES = list(HOST_FUNCTION_DESCRIPTIONS.keys())
ORCH_SYSTEM = f"""Coding agent that creates REUSABLE programs. You are the BRAIN — delegate work, format results.

Pipeline:
1. execute_task(query, title, spec, host_functions, input_schema, runtime_inputs) — searches vault, reuses or creates+tests program. Returns STATUS only.
2. peek_result(ticket_id) — get actual data output to present to user.
3. Present result in plain language. Never show code.

IMPORTANT: execute_task returns STATUS only, not data. After DONE, ALWAYS call peek_result to get data before responding.

Generalization:
- title = reusable program name, NOT specific query. Example: "Find loans by borrower name", NOT "Find loans for borrower ABC"
- input_schema = parameters the program needs. Example: {{"borrower_name": "Name of borrower to look up"}}
- runtime_inputs = specific values for THIS run. Example: {{"borrower_name": "ABC"}}

Other tools:
- iterate_code(ticket_id, feedback) — modify existing program
- close_ticket(ticket_id) — mark done

Rules:
- Never invent ticket_ids. Only use IDs returned by execute_task or iterate_code.
- host_functions choices: {FN_NAMES}"""

ORCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_task",
            "description": "Full pipeline: search vault for reuse, or create+test new program. Returns STATUS only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query"},
                    "title": {"type": "string", "description": "Reusable program title (generalized)"},
                    "spec": {"type": "string", "description": "Detailed specification"},
                    "host_functions": {"type": "array", "items": {"type": "string"}},
                    "input_schema": {"type": "object", "description": "Parameter names to descriptions"},
                    "runtime_inputs": {"type": "object", "description": "Specific values for this run"},
                },
                "required": ["query", "title", "spec", "host_functions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "peek_result",
            "description": "Get latest run output for a ticket. Call after execute_task DONE.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["ticket_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "iterate_code",
            "description": "Modify existing program based on feedback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string"},
                    "feedback": {"type": "string"},
                },
                "required": ["ticket_id", "feedback"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_ticket",
            "description": "Close ticket after user satisfied.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string"},
                },
                "required": ["ticket_id"],
            },
        },
    },
]

# --- Orchestrator conversation examples ---
# Each: user query -> execute_task -> peek_result -> clean response
ORCH_EXAMPLES = [
    # 1. Simple borrower lookup
    {
        "user": "Show me borrower information for Jakarta",
        "execute_task_args": {
            "query": "Show borrower information for Jakarta",
            "title": "List borrowers in city",
            "spec": "Get borrowers in a given city and return their profiles.",
            "host_functions": ["query_borrowers_by_city"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Jakarta"},
        },
        "status": "DONE. Reused TICKET-003 (List borrowers in city). Inputs: {'city': 'Jakarta'}.",
        "peek_data": '[{"name": "Budi Santoso", "credit_score": 720, "monthly_income": 15000000}, {"name": "Siti Rahayu", "credit_score": 680, "monthly_income": 12000000}, {"name": "Ahmad Wijaya", "credit_score": 750, "monthly_income": 18000000}]',
        "response": "Here are the borrowers in Jakarta:\n\n1. **Budi Santoso** — Credit score: 720, Monthly income: Rp 15,000,000\n2. **Siti Rahayu** — Credit score: 680, Monthly income: Rp 12,000,000\n3. **Ahmad Wijaya** — Credit score: 750, Monthly income: Rp 18,000,000",
    },
    # 2. Loan analysis
    {
        "user": "What is the total outstanding amount across all borrowers in all cities?",
        "execute_task_args": {
            "query": "Total outstanding amount across all borrowers in all cities",
            "title": "Total outstanding amount across cities",
            "spec": "For each city get portfolio summary. Sum total_outstanding across all cities. Return grand total and per-city breakdown.",
            "host_functions": ["query_portfolio_summary"],
            "input_schema": {"cities": "Comma-separated city names"},
            "runtime_inputs": {"cities": "Jakarta,Surabaya,Bandung,Medan"},
        },
        "status": "TICKET-005 DONE. Tests passed. Program correctly computes totals.",
        "peek_data": '{"Jakarta": 245500000, "Surabaya": 186200000, "Bandung": 134800000, "Medan": 98500000, "grand_total": 665000000}',
        "response": "The total outstanding amount across all cities is **Rp 665,000,000**.\n\nBreakdown by city:\n- Jakarta: Rp 245,500,000\n- Surabaya: Rp 186,200,000\n- Bandung: Rp 134,800,000\n- Medan: Rp 98,500,000",
    },
    # 3. Complex multi-hop
    {
        "user": "Find borrowers in Jakarta with loans over 50 million, then show their collateral coverage ratio",
        "execute_task_args": {
            "query": "Borrowers in Jakarta with loans over 50 million and collateral coverage",
            "title": "Borrower loans with collateral coverage by city",
            "spec": "Get borrowers in city, filter loans above threshold, compute collateral coverage ratio for each.",
            "host_functions": ["query_borrowers_by_city", "query_loans", "query_collateral"],
            "input_schema": {"city": "City name", "min_amount": "Minimum loan amount"},
            "runtime_inputs": {"city": "Jakarta", "min_amount": "50000000"},
        },
        "status": "TICKET-008 DONE. Tests passed. Collateral coverage computed correctly.",
        "peek_data": '[{"borrower": "Budi Santoso", "loan_amount": 75000000, "collateral_value": 90000000, "coverage_ratio": 1.2}, {"borrower": "Ahmad Wijaya", "loan_amount": 120000000, "collateral_value": 95000000, "coverage_ratio": 0.79}]',
        "response": "Borrowers in Jakarta with loans over 50 million:\n\n| Borrower | Loan Amount | Collateral Value | Coverage Ratio |\n|---|---|---|---|\n| Budi Santoso | 75,000,000 | 90,000,000 | 120% |\n| Ahmad Wijaya | 120,000,000 | 95,000,000 | 79% |\n\nAhmad Wijaya's collateral coverage is below 100%, indicating under-collateralization.",
    },
    # 4. Risk dashboard
    {
        "user": "Build a risk dashboard: for each city show total exposure, NPL ratio, avg collateral coverage, and number of guarantors",
        "execute_task_args": {
            "query": "Risk dashboard by city with exposure, NPL, collateral, guarantors",
            "title": "City risk dashboard",
            "spec": "For each city compute: total loan exposure, NPL ratio (DPD>90), average collateral coverage ratio, and guarantor count.",
            "host_functions": ["query_borrowers_by_city", "query_loans", "query_collateral", "query_guarantors"],
            "input_schema": {"cities": "Comma-separated city names"},
            "runtime_inputs": {"cities": "Jakarta,Surabaya,Bandung"},
        },
        "status": "TICKET-010 DONE. Tests passed. Dashboard metrics computed.",
        "peek_data": '[{"city": "Jakarta", "exposure": 245500000, "npl_ratio": 0.154, "avg_coverage": 0.823, "guarantors": 12}, {"city": "Surabaya", "exposure": 186200000, "npl_ratio": 0.087, "avg_coverage": 0.915, "guarantors": 8}, {"city": "Bandung", "exposure": 134800000, "npl_ratio": 0.221, "avg_coverage": 0.678, "guarantors": 5}]',
        "response": "## Risk Dashboard by City\n\n| City | Total Exposure | NPL Ratio | Avg Collateral Coverage | Guarantors |\n|---|---|---|---|---|\n| Jakarta | 245,500,000 | 15.4% | 82.3% | 12 |\n| Surabaya | 186,200,000 | 8.7% | 91.5% | 8 |\n| Bandung | 134,800,000 | 22.1% | 67.8% | 5 |\n\nBandung has the highest NPL ratio and lowest collateral coverage — highest risk city. Surabaya performs best across all metrics.",
    },
    # 5. Guarantor analysis
    {
        "user": "For each guarantor, show how many distinct borrowers they guarantee and total guarantee amount",
        "execute_task_args": {
            "query": "Guarantor exposure summary across borrowers",
            "title": "Guarantor exposure summary",
            "spec": "Get all borrowers, then guarantors for each. Group by guarantor name, count distinct borrowers and sum guarantee amounts.",
            "host_functions": ["query_borrowers_by_city", "query_guarantors"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Jakarta"},
        },
        "status": "TICKET-012 DONE. Tests passed. Guarantor grouping correct.",
        "peek_data": '[{"guarantor": "PT Bank Mandiri", "borrower_count": 5, "total_amount": 450000000}, {"guarantor": "CV Sukses Jaya", "borrower_count": 3, "total_amount": 280000000}]',
        "response": "Guarantor exposure summary:\n\n1. **PT Bank Mandiri** — Guarantees 5 borrowers, total amount: Rp 450,000,000\n2. **CV Sukses Jaya** — Guarantees 3 borrowers, total amount: Rp 280,000,000",
    },
    # 6. Loan status breakdown
    {
        "user": "Show percentage of loans that are defaulted vs active vs restructured per city",
        "execute_task_args": {
            "query": "Loan status percentage breakdown per city",
            "title": "Loan status distribution by city",
            "spec": "Get borrowers per city, loans for each. Count loans by status. Compute percentage for each status per city.",
            "host_functions": ["query_borrowers_by_city", "query_loans"],
            "input_schema": {"cities": "Comma-separated city names"},
            "runtime_inputs": {"cities": "Jakarta,Surabaya,Bandung"},
        },
        "status": "TICKET-014 DONE. Tests passed. Status percentages computed.",
        "peek_data": '{"Jakarta": {"active": 65.2, "defaulted": 12.1, "restructured": 8.3, "paid_off": 14.4}, "Surabaya": {"active": 71.0, "defaulted": 7.5, "restructured": 5.2, "paid_off": 16.3}}',
        "response": "Loan status distribution by city:\n\n**Jakarta**: Active 65.2%, Defaulted 12.1%, Restructured 8.3%, Paid off 14.4%\n**Surabaya**: Active 71.0%, Defaulted 7.5%, Restructured 5.2%, Paid off 16.3%\n\nJakarta has a higher default rate compared to Surabaya.",
    },
    # 7. Time-based filtering
    {
        "user": "Find loans disbursed in the last 6 months with DPD greater than 30",
        "execute_task_args": {
            "query": "Recent loans with high DPD",
            "title": "Find overdue recent loans",
            "spec": "Get borrowers and loans. Filter to loans disbursed within last 6 months with DPD > threshold.",
            "host_functions": ["query_borrowers_by_city", "query_loans"],
            "input_schema": {"city": "City name", "min_dpd": "Minimum DPD threshold"},
            "runtime_inputs": {"city": "Jakarta", "min_dpd": "30"},
        },
        "status": "TICKET-015 DONE. Tests passed. Found 4 matching loans.",
        "peek_data": '[{"loan_id": "LN-001", "borrower": "Budi", "dpd": 45, "amount": 50000000}, {"loan_id": "LN-012", "borrower": "Siti", "dpd": 62, "amount": 75000000}]',
        "response": "Found 2 recently disbursed loans with DPD > 30:\n\n1. **LN-001** (Budi) — DPD: 45 days, Amount: Rp 50,000,000\n2. **LN-012** (Siti) — DPD: 62 days, Amount: Rp 75,000,000\n\nSiti's loan has the highest delinquency.",
    },
    # 8. Credit score ranking
    {
        "user": "Rank cities by their average borrower credit score, show top and bottom city",
        "execute_task_args": {
            "query": "Rank cities by average credit score",
            "title": "Average credit score per city ranking",
            "spec": "For each city get borrowers, compute average credit score. Rank cities and return top and bottom.",
            "host_functions": ["query_borrowers_by_city"],
            "input_schema": {"cities": "Comma-separated city names"},
            "runtime_inputs": {"cities": "Jakarta,Surabaya,Bandung,Medan"},
        },
        "status": "TICKET-016 DONE. Tests passed. Rankings computed.",
        "peek_data": '{"ranked": [{"city": "Surabaya", "avg_score": 735}, {"city": "Jakarta", "avg_score": 710}, {"city": "Medan", "avg_score": 685}, {"city": "Bandung", "avg_score": 660}], "top": "Surabaya", "bottom": "Bandung"}',
        "response": "City ranking by average credit score:\n\n1. Surabaya — 735 (highest)\n2. Jakarta — 710\n3. Medan — 685\n4. Bandung — 660 (lowest)\n\nSurabaya has the best credit quality, Bandung the weakest.",
    },
    # 9. NPL ratio computation
    {
        "user": "Calculate non-performing loan ratio for each city and compare to portfolio average",
        "execute_task_args": {
            "query": "NPL ratio per city vs portfolio average",
            "title": "NPL ratio comparison by city",
            "spec": "Get borrowers and loans per city. Count loans with DPD>90 as non-performing. Compute NPL ratio per city and overall portfolio.",
            "host_functions": ["query_borrowers_by_city", "query_loans"],
            "input_schema": {"cities": "Comma-separated city names"},
            "runtime_inputs": {"cities": "Jakarta,Surabaya,Bandung"},
        },
        "status": "TICKET-017 DONE. Tests passed. NPL ratios computed.",
        "peek_data": '{"Jakarta": 0.154, "Surabaya": 0.087, "Bandung": 0.221, "portfolio_avg": 0.149}',
        "response": "NPL ratio by city (portfolio average: 14.9%):\n\n- Jakarta: 15.4% (above average)\n- Surabaya: 8.7% (below average)\n- Bandung: 22.1% (above average)\n\nBandung significantly exceeds the portfolio average.",
    },
    # 10. Borrower with highest loan
    {
        "user": "Find the borrower with the highest total loan amount across all their loans",
        "execute_task_args": {
            "query": "Borrower with highest total loan amount",
            "title": "Top borrower by total loan amount",
            "spec": "Get all borrowers, sum loan amounts per borrower, find the one with highest total.",
            "host_functions": ["query_borrowers_by_city", "query_loans"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Jakarta"},
        },
        "status": "TICKET-018 DONE. Tests passed. Top borrower found.",
        "peek_data": '{"borrower": "Ahmad Wijaya", "total_loans": 350000000, "loan_count": 4}',
        "response": "The borrower with the highest total loan amount is **Ahmad Wijaya** with Rp 350,000,000 across 4 loans.",
    },
    # 11. Overpayment analysis
    {
        "user": "List all loans where payment amount exceeds 10% of loan amount, sorted by overpayment ratio descending",
        "execute_task_args": {
            "query": "Loans with overpayment above 10%",
            "title": "Find overpaying loans",
            "spec": "Get borrowers and loans. For each loan compute payment/amount ratio. Filter where ratio > 0.10. Sort descending.",
            "host_functions": ["query_borrowers_by_city", "query_loans", "query_payments"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Jakarta"},
        },
        "status": "TICKET-019 DONE. Tests passed. Overpayment analysis complete.",
        "peek_data": '[{"loan_id": "LN-005", "amount": 30000000, "payment": 5000000, "ratio": 0.167}, {"loan_id": "LN-012", "amount": 80000000, "payment": 10000000, "ratio": 0.125}]',
        "response": "Loans with overpayment ratio above 10%:\n\n1. **LN-005** — Loan: 30M, Payment: 5M, Ratio: 16.7%\n2. **LN-012** — Loan: 80M, Payment: 10M, Ratio: 12.5%",
    },
    # 12. Shared guarantor network
    {
        "user": "Find borrowers who share a guarantor with any defaulted borrower",
        "execute_task_args": {
            "query": "Borrowers sharing guarantors with defaulted borrowers",
            "title": "Find borrowers linked to defaults via guarantors",
            "spec": "Get borrowers and loans. Find defaulted borrowers. Get their guarantors. Find other borrowers with same guarantors.",
            "host_functions": ["query_borrowers_by_city", "query_loans", "query_guarantors"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Jakarta"},
        },
        "status": "TICKET-020 DONE. Tests passed. Found linked borrowers.",
        "peek_data": '[{"borrower": "Siti Rahayu", "shared_guarantor": "PT Bank Mandiri", "defaulted_borrower": "Rudi Hartono"}]',
        "response": "Borrowers sharing guarantors with defaulted borrowers:\n\n- **Siti Rahayu** shares guarantor **PT Bank Mandiri** with defaulted borrower **Rudi Hartono**\n\nThis indicates potential contagion risk through the guarantor network.",
    },
    # 13. Standard deviation
    {
        "user": "Compute standard deviation of loan amounts per city and flag cities with high variance",
        "execute_task_args": {
            "query": "Loan amount variance per city",
            "title": "Loan amount standard deviation by city",
            "spec": "Get borrowers and loans per city. Compute standard deviation of loan amounts. Flag cities where std > mean.",
            "host_functions": ["query_borrowers_by_city", "query_loans", "compute_statistics"],
            "input_schema": {"cities": "Comma-separated city names"},
            "runtime_inputs": {"cities": "Jakarta,Surabaya,Bandung"},
        },
        "status": "TICKET-021 DONE. Tests passed. Statistics computed.",
        "peek_data": '{"Jakarta": {"mean": 65000000, "std": 45000000, "high_variance": false}, "Bandung": {"mean": 40000000, "std": 52000000, "high_variance": true}}',
        "response": "Loan amount standard deviation by city:\n\n- **Jakarta**: Mean 65M, Std 45M — Normal variance\n- **Bandung**: Mean 40M, Std 52M — **High variance** (std exceeds mean)\n\nBandung's loan amounts are highly dispersed, suggesting inconsistent lending patterns.",
    },
    # 14. Compare cities
    {
        "user": "Compare average loan amounts between Bandung and Surabaya",
        "execute_task_args": {
            "query": "Compare average loan amounts Bandung vs Surabaya",
            "title": "Average loan amount comparison by city",
            "spec": "Get portfolio summary for each city. Compare average loan amounts.",
            "host_functions": ["query_portfolio_summary"],
            "input_schema": {"cities": "Comma-separated city names"},
            "runtime_inputs": {"cities": "Bandung,Surabaya"},
        },
        "status": "DONE. Reused TICKET-016 (Average loan comparison). Inputs: {'cities': 'Bandung,Surabaya'}.",
        "peek_data": '{"Bandung": {"avg_loan_amount": 42000000}, "Surabaya": {"avg_loan_amount": 58000000}}',
        "response": "Average loan amount comparison:\n\n- Surabaya: Rp 58,000,000\n- Bandung: Rp 42,000,000\n\nSurabaya's average loan amount is 38% higher than Bandung's.",
    },
    # 15. Weighted average interest rate
    {
        "user": "Calculate weighted average interest rate by loan amount for each product type",
        "execute_task_args": {
            "query": "Weighted average interest rate by product type",
            "title": "Weighted average interest rate by product",
            "spec": "Get loans. Group by product type. Compute weighted average interest rate where weight = loan amount.",
            "host_functions": ["query_borrowers_by_city", "query_loans"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Jakarta"},
        },
        "status": "TICKET-022 DONE. Tests passed. Weighted rates computed.",
        "peek_data": '{"KPR": 8.5, "KTA": 14.2, "KMG": 11.0}',
        "response": "Weighted average interest rate by product type:\n\n- KPR (Mortgage): 8.5%\n- KMG (Multi-purpose): 11.0%\n- KTA (Unsecured): 14.2%\n\nUnsecured loans (KTA) carry the highest effective rate.",
    },
    # 16. Borrower contact lookup
    {
        "user": "Get contact info for borrower Budi Santoso",
        "execute_task_args": {
            "query": "Get borrower contact info",
            "title": "Get borrower contact info",
            "spec": "Look up borrower profile. Return name, phone, email, city.",
            "host_functions": ["query_borrower"],
            "input_schema": {"borrower_name": "Name of borrower"},
            "runtime_inputs": {"borrower_name": "Budi Santoso"},
        },
        "status": "DONE. Reused TICKET-001 (Get borrower contact info). Inputs: {'borrower_name': 'Budi Santoso'}.",
        "peek_data": '{"name": "Budi Santoso", "phone": "+62812345678", "email": "budi@email.com", "city": "Jakarta"}',
        "response": "Contact info for Budi Santoso:\n\n- Phone: +62812345678\n- Email: budi@email.com\n- City: Jakarta",
    },
    # 17. Debt-to-income ratio
    {
        "user": "Show borrowers with highest debt-to-income ratio in Surabaya",
        "execute_task_args": {
            "query": "Borrowers with highest debt-to-income ratio in Surabaya",
            "title": "Rank borrowers by debt to income ratio",
            "spec": "Get borrowers in city. For each compute total_debt / monthly_income. Return ranked list.",
            "host_functions": ["query_borrowers_by_city", "query_loans"],
            "input_schema": {"city": "City name", "top_n": "Number of results"},
            "runtime_inputs": {"city": "Surabaya", "top_n": "5"},
        },
        "status": "TICKET-023 DONE. Tests passed. DTI ratios computed.",
        "peek_data": '[{"name": "Dewi Lestari", "debt": 200000000, "income": 8000000, "dti": 25.0}, {"name": "Agus Pratama", "debt": 150000000, "income": 10000000, "dti": 15.0}]',
        "response": "Top borrowers by debt-to-income ratio in Surabaya:\n\n1. **Dewi Lestari** — DTI: 25.0x (Debt: 200M, Income: 8M/month)\n2. **Agus Pratama** — DTI: 15.0x (Debt: 150M, Income: 10M/month)\n\nDewi Lestari has a very high leverage ratio.",
    },
    # 18. Portfolio summary
    {
        "user": "Give me the portfolio overview for Bandung",
        "execute_task_args": {
            "query": "Portfolio overview for Bandung",
            "title": "Get portfolio overview for a city",
            "spec": "Get portfolio summary. Return total_borrowers, total_loans, avg_loan_amount, npl_ratio.",
            "host_functions": ["query_portfolio_summary"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Bandung"},
        },
        "status": "TICKET-024 DONE. Tests passed. Summary retrieved.",
        "peek_data": '{"total_borrowers": 1250, "total_loans": 3400, "avg_loan_amount": 42000000, "npl_ratio": 0.18}',
        "response": "Portfolio overview for Bandung:\n\n- Total borrowers: 1,250\n- Total loans: 3,400\n- Average loan amount: Rp 42,000,000\n- NPL ratio: 18.0%",
    },
    # 19. Payment analysis
    {
        "user": "Show payment success rate by payment method for borrower Siti Rahayu",
        "execute_task_args": {
            "query": "Payment success rate by method for borrower",
            "title": "Payment success rate by channel",
            "spec": "Get borrower loans and payments. Group by payment method. Compute success rate per method.",
            "host_functions": ["query_borrower", "query_loans", "query_payments"],
            "input_schema": {"borrower_name": "Borrower name"},
            "runtime_inputs": {"borrower_name": "Siti Rahayu"},
        },
        "status": "TICKET-025 DONE. Tests passed. Success rates computed.",
        "peek_data": '{"bank_transfer": 0.95, "mobile_payment": 0.88, "cash": 1.0}',
        "response": "Payment success rate for Siti Rahayu by method:\n\n- Cash: 100%\n- Bank transfer: 95%\n- Mobile payment: 88%\n\nMobile payments have the lowest success rate.",
    },
    # 20. Collection records
    {
        "user": "Show collection history timeline for borrower Ahmad Wijaya",
        "execute_task_args": {
            "query": "Collection history for borrower",
            "title": "Borrower collection history timeline",
            "spec": "Get borrower loans and collection records. Merge and sort by date. Return timeline.",
            "host_functions": ["query_borrower", "query_loans", "query_collection_records"],
            "input_schema": {"borrower_name": "Borrower name"},
            "runtime_inputs": {"borrower_name": "Ahmad Wijaya"},
        },
        "status": "TICKET-026 DONE. Tests passed. Timeline constructed.",
        "peek_data": '[{"date": "2025-01-15", "action": "phone_call", "outcome": "promise_to_pay"}, {"date": "2025-02-01", "action": "field_visit", "outcome": "partial_payment"}]',
        "response": "Collection timeline for Ahmad Wijaya:\n\n- 2025-01-15: Phone call — Promise to pay\n- 2025-02-01: Field visit — Partial payment received",
    },
    # 21. Delinquency stats
    {
        "user": "Show delinquency bucket details",
        "execute_task_args": {
            "query": "Delinquency bucket breakdown",
            "title": "List delinquency buckets with loan counts",
            "spec": "Get all delinquency stats. Return list of buckets with loan_count sorted descending.",
            "host_functions": ["query_delinquency_stats"],
            "input_schema": {},
            "runtime_inputs": {},
        },
        "status": "TICKET-027 DONE. Tests passed. Buckets retrieved.",
        "peek_data": '[{"bucket": "current", "loan_count": 5000}, {"bucket": "1-30", "loan_count": 800}, {"bucket": "31-60", "loan_count": 350}, {"bucket": "61-90", "loan_count": 150}, {"bucket": "90+", "loan_count": 200}]',
        "response": "Delinquency buckets:\n\n| Bucket | Loan Count |\n|---|---|\n| Current | 5,000 |\n| 1-30 DPD | 800 |\n| 31-60 DPD | 350 |\n| 61-90 DPD | 150 |\n| 90+ DPD | 200 |\n\nTotal delinquent (DPD > 0): 1,500 loans.",
    },
    # 22. Correlation analysis
    {
        "user": "Is there a correlation between credit score and total outstanding loans?",
        "execute_task_args": {
            "query": "Correlation between credit score and total outstanding",
            "title": "Credit score vs outstanding correlation",
            "spec": "Get borrowers across cities. Extract credit_score and total_outstanding. Compute correlation.",
            "host_functions": ["query_borrowers_by_city", "compute_correlation"],
            "input_schema": {"cities": "Comma-separated cities"},
            "runtime_inputs": {"cities": "Jakarta,Surabaya,Bandung"},
        },
        "status": "TICKET-028 DONE. Tests passed. Correlation computed.",
        "peek_data": '{"correlation": -0.35, "borrower_count": 150, "interpretation": "weak negative"}',
        "response": "The correlation between credit score and total outstanding is **-0.35** (weak negative), based on 150 borrowers.\n\nThis suggests borrowers with higher credit scores tend to have slightly lower outstanding balances, but the relationship is weak.",
    },
    # 23. Tabulated data
    {
        "user": "Show top 10 borrowers in Jakarta sorted by credit score",
        "execute_task_args": {
            "query": "Top borrowers in Jakarta by credit score",
            "title": "Sorted borrower table with tabulate",
            "spec": "Get borrowers in city. Sort by credit_score descending. Return top N with key columns.",
            "host_functions": ["query_borrowers_by_city", "tabulate_data"],
            "input_schema": {"city": "City name", "limit": "Max borrowers"},
            "runtime_inputs": {"city": "Jakarta", "limit": "10"},
        },
        "status": "DONE. Reused TICKET-004 (Sorted borrower table). Inputs: {'city': 'Jakarta', 'limit': '10'}.",
        "peek_data": '[{"name": "Ahmad Wijaya", "credit_score": 750, "monthly_income": 18000000}, {"name": "Budi Santoso", "credit_score": 720, "monthly_income": 15000000}]',
        "response": "Top borrowers in Jakarta by credit score:\n\n1. Ahmad Wijaya — Score: 750, Income: 18M\n2. Budi Santoso — Score: 720, Income: 15M",
    },
    # 24. Loan maturity analysis
    {
        "user": "What's the loan maturity breakdown for Surabaya?",
        "execute_task_args": {
            "query": "Loan maturity breakdown for Surabaya",
            "title": "Loan maturity analysis",
            "spec": "Get borrowers and loans. Categorize loans by remaining maturity: short (<6mo), medium (6-18mo), long (>18mo).",
            "host_functions": ["query_borrowers_by_city", "query_loans"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Surabaya"},
        },
        "status": "TICKET-029 DONE. Tests passed. Maturity categorized.",
        "peek_data": '{"short_term": 45, "medium_term": 120, "long_term": 85}',
        "response": "Loan maturity breakdown for Surabaya:\n\n- Short-term (<6 months): 45 loans\n- Medium-term (6-18 months): 120 loans\n- Long-term (>18 months): 85 loans\n\nMajority of loans are medium-term.",
    },
    # 25. Reuse scenario with different runtime inputs
    {
        "user": "Show me borrowers in Medan",
        "execute_task_args": {
            "query": "Show borrowers in Medan",
            "title": "List borrowers in city",
            "spec": "Get borrowers in a given city and return their profiles.",
            "host_functions": ["query_borrowers_by_city"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Medan"},
        },
        "status": "DONE. Reused TICKET-003 (List borrowers in city). Inputs: {'city': 'Medan'}.",
        "peek_data": '[{"name": "Rina Hutapea", "credit_score": 690, "monthly_income": 11000000}, {"name": "Joko Susilo", "credit_score": 710, "monthly_income": 14000000}]',
        "response": "Borrowers in Medan:\n\n1. **Joko Susilo** — Credit score: 710, Monthly income: Rp 14,000,000\n2. **Rina Hutapea** — Credit score: 690, Monthly income: Rp 11,000,000",
    },
    # 26. Payment completion timeline
    {
        "user": "Show the payment completion timeline for loan LN-005",
        "execute_task_args": {
            "query": "Payment timeline for specific loan",
            "title": "Payment completion timeline for loan",
            "spec": "Get payments for loan. Sort by date. Compute running total of completed payments.",
            "host_functions": ["query_payments"],
            "input_schema": {"loan_id": "Loan ID"},
            "runtime_inputs": {"loan_id": "LN-005"},
        },
        "status": "TICKET-030 DONE. Tests passed. Timeline computed.",
        "peek_data": '[{"date": "2025-01-15", "amount": 2000000, "running_total": 2000000}, {"date": "2025-02-15", "amount": 2000000, "running_total": 4000000}]',
        "response": "Payment timeline for loan LN-005:\n\n| Date | Payment | Running Total |\n|---|---|---|\n| 2025-01-15 | 2,000,000 | 2,000,000 |\n| 2025-02-15 | 2,000,000 | 4,000,000 |",
    },
    # 27. Multi-city borrower count
    {
        "user": "How many borrowers are in each city?",
        "execute_task_args": {
            "query": "Borrower count per city",
            "title": "Multi-city borrower count comparison",
            "spec": "Get portfolio summary for each city. Return city and total_borrowers sorted descending.",
            "host_functions": ["query_portfolio_summary"],
            "input_schema": {"cities": "Comma-separated city names"},
            "runtime_inputs": {"cities": "Jakarta,Surabaya,Bandung,Medan"},
        },
        "status": "TICKET-031 DONE. Tests passed. Counts retrieved.",
        "peek_data": '[{"city": "Jakarta", "total_borrowers": 2500}, {"city": "Surabaya", "total_borrowers": 1800}, {"city": "Bandung", "total_borrowers": 1250}, {"city": "Medan", "total_borrowers": 950}]',
        "response": "Borrower count by city:\n\n1. Jakarta: 2,500\n2. Surabaya: 1,800\n3. Bandung: 1,250\n4. Medan: 950\n\nTotal: 6,500 borrowers across all cities.",
    },
    # 28. Collateral type distribution
    {
        "user": "What types of collateral are most common in Jakarta?",
        "execute_task_args": {
            "query": "Collateral type distribution in Jakarta",
            "title": "Collateral type distribution for city",
            "spec": "Get borrowers, loans, collateral. Count items by collateral type.",
            "host_functions": ["query_borrowers_by_city", "query_loans", "query_collateral"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Jakarta"},
        },
        "status": "TICKET-032 DONE. Tests passed. Type distribution computed.",
        "peek_data": '{"property": 45, "vehicle": 30, "deposit": 15, "equipment": 10}',
        "response": "Collateral types in Jakarta:\n\n- Property: 45 items (most common)\n- Vehicle: 30 items\n- Deposit: 15 items\n- Equipment: 10 items",
    },
    # 29. Interactive-style (simple query)
    {
        "user": "Look up borrower Dewi Lestari",
        "execute_task_args": {
            "query": "Look up borrower Dewi Lestari",
            "title": "Get borrower contact info",
            "spec": "Look up borrower profile by name. Return key profile fields.",
            "host_functions": ["query_borrower"],
            "input_schema": {"borrower_name": "Name of borrower"},
            "runtime_inputs": {"borrower_name": "Dewi Lestari"},
        },
        "status": "DONE. Reused TICKET-001 (Get borrower contact info). Inputs: {'borrower_name': 'Dewi Lestari'}.",
        "peek_data": '{"name": "Dewi Lestari", "city": "Surabaya", "credit_score": 695, "monthly_income": 8000000, "total_loans": 3}',
        "response": "Borrower profile for Dewi Lestari:\n\n- City: Surabaya\n- Credit score: 695\n- Monthly income: Rp 8,000,000\n- Total loans: 3",
    },
    # 30. Network analysis
    {
        "user": "Show the borrower-guarantor network for Bandung",
        "execute_task_args": {
            "query": "Borrower-guarantor network analysis for Bandung",
            "title": "Network analysis of borrower-guarantor relationships",
            "spec": "Get borrowers and guarantors. Build edge list. Analyze network centrality.",
            "host_functions": ["query_borrowers_by_city", "query_guarantors", "analyze_network"],
            "input_schema": {"city": "City name"},
            "runtime_inputs": {"city": "Bandung"},
        },
        "status": "TICKET-033 DONE. Tests passed. Network analyzed.",
        "peek_data": '{"most_central": "PT Asuransi Jaya", "degree": 8, "nodes": 25, "edges": 32}',
        "response": "Borrower-guarantor network for Bandung:\n\n- 25 nodes, 32 connections\n- Most central entity: **PT Asuransi Jaya** (connected to 8 borrowers)\n\nThis guarantor has significant exposure across multiple borrowers.",
    },
]


def build_fn_docs(fn_list: list[str]) -> str:
    return "\n".join(
        f"- {HOST_FUNCTION_DESCRIPTIONS[f]}" for f in fn_list
        if f in HOST_FUNCTION_DESCRIPTIONS
    )


def build_user_prompt(spec: str, host_functions: list[str],
                      input_schema: dict[str, str], tests: str) -> str:
    fn_doc = build_fn_docs(host_functions)
    parts = [f"## Spec\n{spec}"]
    parts.append(f"\n## Host functions\n{fn_doc}")
    if input_schema:
        schema_lines = "\n".join(f"  - {k}: {v}" for k, v in input_schema.items())
        parts.append(f"\n## Input parameters (in `inputs` dict)\n{schema_lines}")
    parts.append(f"\n## Tests (code must pass these)\n```\n{tests}\n```")
    return "\n".join(parts)


def triple_to_chat(triple: dict) -> dict:
    user = build_user_prompt(
        triple["spec"],
        triple["host_functions"],
        triple.get("input_schema", {}),
        triple["tests"],
    )
    return {
        "messages": [
            {"role": "system", "content": CODER_SYSTEM},
            {"role": "user", "content": user},
            {"role": "assistant", "content": triple["code"]},
        ]
    }


def orch_example_to_chat(ex: dict) -> dict:
    """Convert orchestrator example to chat format with tool calls."""
    ticket_id = "TICKET-001"
    for word in ex["status"].split():
        if word.startswith("TICKET-"):
            ticket_id = word.rstrip(".")
            break

    messages = [
        {"role": "system", "content": ORCH_SYSTEM},
        {"role": "user", "content": ex["user"]},
        # Assistant calls execute_task
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "type": "function",
                "id": "call_1",
                "function": {
                    "name": "execute_task",
                    "arguments": json.dumps(ex["execute_task_args"]),
                },
            }],
        },
        # Tool returns status
        {"role": "tool", "tool_call_id": "call_1", "name": "execute_task", "content": ex["status"]},
        # Assistant calls peek_result
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "type": "function",
                "id": "call_2",
                "function": {
                    "name": "peek_result",
                    "arguments": json.dumps({"ticket_id": ticket_id}),
                },
            }],
        },
        # Tool returns data
        {"role": "tool", "tool_call_id": "call_2", "name": "peek_result", "content": ex["peek_data"]},
        # Assistant gives clean response
        {"role": "assistant", "content": ex["response"]},
    ]
    return {"messages": messages, "tools": ORCH_TOOLS}


def load_vault_examples() -> list[dict]:
    examples = []
    if not VAULT_DIR.exists():
        return examples
    for ticket_dir in sorted(VAULT_DIR.iterdir()):
        if not ticket_dir.is_dir():
            continue
        yaml_file = ticket_dir / "ticket.yaml"
        sol_file = ticket_dir / "solution.py"
        test_file = ticket_dir / "test_solution.py"
        spec_file = ticket_dir / "spec.md"
        if not all(f.exists() for f in [yaml_file, sol_file, test_file, spec_file]):
            continue

        yaml_text = yaml_file.read_text()
        quality = ""
        for line in yaml_text.split("\n"):
            if line.startswith("quality:"):
                quality = line.split(":", 1)[1].strip()
        if quality != "good":
            continue

        host_functions = []
        in_hf = False
        for line in yaml_text.split("\n"):
            if line.startswith("host_function_allowlist:"):
                in_hf = True
                continue
            if in_hf:
                if line.startswith("- "):
                    host_functions.append(line[2:].strip())
                else:
                    in_hf = False

        input_schema = {}
        in_schema = False
        for line in yaml_text.split("\n"):
            if line.startswith("input_schema:"):
                in_schema = True
                continue
            if in_schema:
                if line.startswith("  "):
                    parts = line.strip().split(":", 1)
                    if len(parts) == 2:
                        input_schema[parts[0].strip()] = parts[1].strip()
                else:
                    in_schema = False

        spec = spec_file.read_text().strip()
        code = sol_file.read_text().strip()
        tests = test_file.read_text().strip()

        if not code or not tests:
            continue

        examples.append({
            "spec": spec,
            "host_functions": host_functions,
            "input_schema": input_schema,
            "tests": tests,
            "code": code,
        })
    return examples


def main():
    triples = []
    if TRIPLES_FILE.exists():
        with open(TRIPLES_FILE) as f:
            triples = json.load(f)

    vault = load_vault_examples()
    orch = ORCH_EXAMPLES

    print(f"Generated triples: {len(triples)}")
    print(f"Vault examples: {len(vault)}")
    print(f"Orchestrator examples: {len(orch)}")

    all_chats = []

    # Code generation examples
    for ex in triples + vault:
        all_chats.append(triple_to_chat(ex))

    # Orchestrator tool-calling examples
    for ex in orch:
        all_chats.append(orch_example_to_chat(ex))

    print(f"Total training examples: {len(all_chats)}")

    with open(OUT_FILE, "w") as f:
        for chat in all_chats:
            f.write(json.dumps(chat) + "\n")

    # Stats
    sizes = []
    for chat in all_chats:
        total = 0
        for m in chat["messages"]:
            c = m.get("content", "")
            if c:
                total += len(c)
            if "tool_calls" in m:
                total += len(json.dumps(m["tool_calls"]))
        sizes.append(total)

    avg_chars = sum(sizes) / len(sizes)
    print(f"\nAvg example size: {avg_chars:.0f} chars (~{avg_chars/4:.0f} tokens)")
    print(f"Total dataset: {sum(sizes)} chars (~{sum(sizes)//4} tokens)")
    print(f"Saved to {OUT_FILE}")


if __name__ == "__main__":
    main()
