"""Batch-generate training triples (spec, tests, code) using DeepSeek pipeline."""
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_factory.agents.coder import MONTY_LIMITATIONS
from code_factory.sandbox.host_functions import (
    HOST_FUNCTION_DESCRIPTIONS,
    HOST_FUNCTIONS,
    build_external_lookup,
)
from code_factory.sandbox.runner import run_tests

# ---------------------------------------------------------------------------
# Task definitions — diverse combos of host functions + patterns
# ---------------------------------------------------------------------------

TASKS = [
    # --- BATCH 1: Original 30 ---
    {
        "title": "List all borrower transactions in date range",
        "spec": "Given a borrower name, find their transactions within a date range. Filter by start_date and end_date from inputs. Return list of matching transactions sorted by date.",
        "host_functions": ["query_borrower", "query_transactions"],
        "input_schema": {"borrower_name": "Name of borrower", "start_date": "YYYY-MM-DD start", "end_date": "YYYY-MM-DD end"},
    },
    {
        "title": "Total payment amount by method for a loan",
        "spec": "Given a loan ID, get all payments and compute total amount grouped by payment method. Return dict mapping method to total amount.",
        "host_functions": ["query_payments"],
        "input_schema": {"loan_id": "Loan ID to analyze"},
    },
    {
        "title": "Find highest value collateral across borrower loans",
        "spec": "Given borrower name, find all their loans, then all collateral for each loan. Return the single collateral item with highest appraised_value. Assign to result at module level.",
        "host_functions": ["query_borrower", "query_loans", "query_collateral"],
        "input_schema": {"borrower_name": "Name of borrower"},
    },
    {
        "title": "Portfolio NPL ratio by city",
        "spec": "Query portfolio summary for each city in the input list. Return dict mapping city to npl_ratio, sorted by npl_ratio descending.",
        "host_functions": ["query_portfolio_summary"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "Borrower risk summary with statistics",
        "spec": "Get all borrowers in a city, extract their credit scores. Compute descriptive statistics (mean, median, std, min, max) on credit scores. Return statistics dict.",
        "host_functions": ["query_borrowers_by_city", "compute_statistics"],
        "input_schema": {"city": "City name", "limit": "Max borrowers to fetch"},
    },
    {
        "title": "Compare delinquency recovery rates across buckets",
        "spec": "Get delinquency stats for all buckets. Sort by recovery_rate ascending. Assign sorted list of dicts with bucket and recovery_rate to result.",
        "host_functions": ["query_delinquency_stats"],
        "input_schema": {},
    },
    {
        "title": "Borrower collection history timeline",
        "spec": "Given borrower name, get all their loans, then all collection records for each loan. Merge and sort by action_date. Assign result as timeline list of dicts with loan_id, action_date, action_type, outcome.",
        "host_functions": ["query_borrower", "query_loans", "query_collection_records"],
        "input_schema": {"borrower_name": "Name of borrower"},
    },
    {
        "title": "Average interest rate by product type in city",
        "spec": "Get borrowers in a city (limit 5), then get loans for each borrower. Group loans by product type and compute average interest_rate per product. Assign result as dict mapping product to avg interest rate.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Guarantor exposure summary",
        "spec": "Get borrowers in a city (limit 5), then get guarantors for each borrower. For each unique guarantor (by name), sum their total guarantee_amount across all borrowers. Return top N guarantors by total exposure as list of dicts.",
        "host_functions": ["query_borrowers_by_city", "query_guarantors"],
        "input_schema": {"city": "City name", "top_n": "Number of top guarantors to return"},
    },
    {
        "title": "Loan disbursement trend by month",
        "spec": "Get borrowers in a city (limit 5), then loans for each. Group loans by disbursement month (YYYY-MM extracted from disbursed_date string using string slicing [:7]). For each month compute count and total amount. Assign result as list sorted by month.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Find defaulted loans with high collateral value",
        "spec": "Get borrowers in a city. For each borrower get loans. Filter to loans where status is 'defaulted'. For each defaulted loan get collateral. Return list of loans where sum of collateral appraised_value exceeds loan amount.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "query_collateral"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Payment success rate by channel",
        "spec": "Given borrower name, get all loans, then all payments for each loan. Group payments by method field. For each method compute success_rate = count where status is 'completed' divided by total count. Assign result as dict mapping method to success_rate.",
        "host_functions": ["query_borrower", "query_loans", "query_payments"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Cross-city credit score comparison",
        "spec": "Given list of cities, get borrowers in each. Compute average credit score per city. Also compute correlation between credit_score and monthly_income across all borrowers combined. Return per-city averages and overall correlation.",
        "host_functions": ["query_borrowers_by_city", "compute_correlation"],
        "input_schema": {"cities": "Comma-separated cities"},
    },
    {
        "title": "Borrower debt-to-income ratio ranking",
        "spec": "Get borrowers in a city. For each borrower, get loans and compute total outstanding debt (sum of loan amounts for active loans). Compute debt-to-income ratio = total_debt / monthly_income. Return list sorted by ratio descending.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name", "limit": "Max borrowers"},
    },
    {
        "title": "Late fee analysis for borrower",
        "spec": "Given borrower name, get all loans, then all payments. Sum total late_fee across all payments. Group by loan_id. Return dict with per-loan late fees and grand total.",
        "host_functions": ["query_borrower", "query_loans", "query_payments"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Interactive loan lookup with confirmation",
        "spec": "Ask user for borrower name using ask_user. Look up borrower using query_borrower. Ask if they want loan details using ask_confirm. If confirmed (True), get loans using query_loans with borrower_id. Assign result = {'borrower': borrower, 'loans': loans} at module level. If not confirmed, assign result = {'borrower': borrower, 'loans': []}.",
        "host_functions": ["ask_user", "ask_confirm", "query_borrower", "query_loans"],
        "input_schema": {},
    },
    {
        "title": "Aggregate loans by status using pandas bridge",
        "spec": "Get borrowers in a city, then all loans. Build records list. Use aggregate_data to group by status and compute sum of amount and count. Return aggregated result.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "aggregate_data"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Pivot loan amounts by product and status",
        "spec": "Get borrowers in a city, then loans. Build flat records. Use pivot_data to create pivot table with product as index, status as columns, amount as values (sum). Return pivot result.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "pivot_data"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Network analysis of borrower-guarantor relationships",
        "spec": "Get borrowers in a city. For each, get guarantors. Build edge list [[borrower_name, guarantor_name]]. Use analyze_network to find centrality. Return top 5 most central nodes.",
        "host_functions": ["query_borrowers_by_city", "query_guarantors", "analyze_network"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Sorted borrower table with tabulate",
        "spec": "Get borrowers in a city. Use tabulate_data to sort by credit_score descending and select columns: name, credit_score, monthly_income, total_loans. Return sorted table.",
        "host_functions": ["query_borrowers_by_city", "tabulate_data"],
        "input_schema": {"city": "City name", "limit": "Max borrowers"},
    },
    {
        "title": "Loan maturity analysis",
        "spec": "Get borrowers in a city (limit 5), then loans. For each loan, parse disbursed_date string (YYYY-MM-DD) by splitting on '-' to get year and month as ints. Compute approximate months_elapsed = (2025 - year) * 12 + (6 - month). Compute months_remaining = tenor_months - months_elapsed. Categorize: short_term (remaining<6), medium_term (6 to 18), long_term (>18). Assign result as dict mapping category to count. Do NOT use datetime module.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Find related borrowers through guarantor network",
        "spec": "Given borrower name, find their guarantors. Then find all other borrowers who share any of the same guarantors. Build edge list and use find_related_entities to find connections up to depth 2. Return related entities.",
        "host_functions": ["query_borrower", "query_loans", "query_guarantors", "query_borrowers_by_city", "find_related_entities"],
        "input_schema": {"borrower_name": "Borrower name", "city": "City to search"},
    },
    {
        "title": "DPD distribution statistics",
        "spec": "Get borrowers in a city, then loans. Collect all DPD values. Use compute_statistics to get distribution stats. Also count loans in each DPD bucket (current 0, 1-30, 31-60, 61-90, 90+). Return stats and bucket counts.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "compute_statistics"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Monthly collection effectiveness report",
        "spec": "Get borrowers in a city (limit 5), all loans, all collection records. Group collection records by month (action_date[:7]). For each month compute: total_actions count, success_count where outcome is in ('promise_to_pay', 'partial_payment', 'paid_in_full'), and effectiveness_rate = success_count/total_actions. Assign result as list sorted by month.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "query_collection_records"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Portfolio concentration by product type",
        "spec": "Get borrowers in a city, then loans. Compute total amount per product type and overall total. Return dict mapping product to dict with amount and percentage of total.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Interactive city comparison with user choice",
        "spec": "Present city choices (Jakarta, Surabaya, Bandung, Medan) using ask_choice. Get borrowers in selected city. Ask user for minimum credit score threshold using ask_number. Filter borrowers above threshold. Return filtered list.",
        "host_functions": ["ask_choice", "ask_number", "query_borrowers_by_city"],
        "input_schema": {"cities": "Available cities list"},
    },
    {
        "title": "Collateral coverage ratio by loan product",
        "spec": "Get borrowers in a city (limit 5), then loans, then collateral for each loan. Compute collateral_coverage = total_collateral_value / loan_amount for each loan. Group by product type and compute average coverage ratio. Return dict mapping product to avg coverage.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "query_collateral"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Borrower payment consistency score",
        "spec": "Given borrower name, get loans then payments for each. For each loan compute on_time_ratio = count of payments with status 'completed' / total payments. Average across all loans for overall consistency score. Assign result as dict with per_loan scores list and overall score.",
        "host_functions": ["query_borrower", "query_loans", "query_payments"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Top performing loans by interest earned",
        "spec": "Get borrowers in a city, then loans. For each loan with status 'active', estimate interest_earned = amount * interest_rate / 100 * tenor_months / 12. Sort by interest_earned descending. Return top N as list of dicts with loan_id, amount, interest_rate, interest_earned.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name", "top_n": "Number of top loans"},
    },
    {
        "title": "Restructured loan recovery analysis",
        "spec": "Get borrowers in a city (limit 5), then loans. Filter to loans where status is 'restructured'. For each, get payment history. Compute total_recovered = sum of payment amounts where status is 'completed'. Assign result as list of dicts with loan_id, original_amount, total_recovered, recovery_pct.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "query_payments"],
        "input_schema": {"city": "City name"},
    },

    # --- BATCH 2: Simple lookups and single-hop ---
    {
        "title": "Get borrower contact info",
        "spec": "Given borrower name, look up borrower profile. Return dict with name, phone, email, city only.",
        "host_functions": ["query_borrower"],
        "input_schema": {"borrower_name": "Name of borrower"},
    },
    {
        "title": "Count loans by status for borrower",
        "spec": "Given borrower name, get borrower then loans. Count how many loans in each status (active, paid_off, defaulted, restructured). Return dict mapping status to count.",
        "host_functions": ["query_borrower", "query_loans"],
        "input_schema": {"borrower_name": "Name of borrower"},
    },
    {
        "title": "Get loan detail with all sub-records",
        "spec": "Given loan_id from inputs, use query_loan_details to get full details including payments, collateral, and collection_records. Assign result as the complete detail dict. Do not use dir() or inspect the object.",
        "host_functions": ["query_loan_details"],
        "input_schema": {"loan_id": "Loan ID"},
    },
    {
        "title": "List borrower names in a city",
        "spec": "Given city and limit, get borrowers and return just a list of their names sorted alphabetically.",
        "host_functions": ["query_borrowers_by_city"],
        "input_schema": {"city": "City name", "limit": "Max borrowers"},
    },
    {
        "title": "Get portfolio overview for a city",
        "spec": "Given city, get portfolio summary. Return dict with total_borrowers, total_loans, avg_loan_amount, npl_ratio.",
        "host_functions": ["query_portfolio_summary"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "List delinquency buckets with loan counts",
        "spec": "Get all delinquency stats (pass bucket=None). Return list of dicts with bucket name and loan_count, sorted by loan_count descending.",
        "host_functions": ["query_delinquency_stats"],
        "input_schema": {},
    },
    {
        "title": "Get single delinquency bucket details",
        "spec": "Given bucket name from inputs (e.g. '31-60'), call query_delinquency_stats with that bucket. The result is a list — return first element. Assign result as the single bucket dict.",
        "host_functions": ["query_delinquency_stats"],
        "input_schema": {"bucket": "DPD bucket name like 1-30 or 61-90"},
    },
    {
        "title": "Recent transactions for borrower",
        "spec": "Given borrower name and limit (convert to int) from inputs, get borrower profile then query_transactions with borrower_id and the limit. Sort by date descending. Assign result as sorted list.",
        "host_functions": ["query_borrower", "query_transactions"],
        "input_schema": {"borrower_name": "Borrower name", "limit": "Max transactions"},
    },

    # --- BATCH 3: Filtering patterns ---
    {
        "title": "Find high credit score borrowers",
        "spec": "Get borrowers in a city. Filter to those with credit_score above a threshold from inputs. Return filtered list sorted by credit_score descending.",
        "host_functions": ["query_borrowers_by_city"],
        "input_schema": {"city": "City name", "min_score": "Minimum credit score threshold"},
    },
    {
        "title": "Find active loans above amount threshold",
        "spec": "Given borrower name, get loans. Filter to active loans with amount above min_amount from inputs (convert to int). Assign result as filtered list.",
        "host_functions": ["query_borrower", "query_loans"],
        "input_schema": {"borrower_name": "Borrower name", "min_amount": "Minimum loan amount"},
    },
    {
        "title": "Filter payments by status",
        "spec": "Given loan_id and payment_status from inputs, get all payments for the loan. Filter to those matching the given status. Return count and total amount of matching payments.",
        "host_functions": ["query_payments"],
        "input_schema": {"loan_id": "Loan ID", "payment_status": "Status to filter by"},
    },
    {
        "title": "Find overdue loans in city above DPD threshold",
        "spec": "Get borrowers in a city, then loans for each. Filter to loans where dpd (int) exceeds int(min_dpd) from inputs. Assign result as list of dicts with borrower_name, loan_id, dpd, amount.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name", "min_dpd": "Minimum days past due"},
    },
    {
        "title": "Find seized collateral items",
        "spec": "Given borrower name, get loans, then collateral for each. Filter collateral where status is 'seized'. Return list of seized items with loan_id included.",
        "host_functions": ["query_borrower", "query_loans", "query_collateral"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Filter collection records by action type",
        "spec": "Given loan_id and action_type from inputs, get collection records. Filter to records matching action_type. Return matching records sorted by action_date.",
        "host_functions": ["query_collection_records"],
        "input_schema": {"loan_id": "Loan ID", "action_type": "Action type to filter"},
    },
    {
        "title": "Find low income borrowers with high debt",
        "spec": "Get borrowers in a city. Filter to those with monthly_income below income_threshold. For each, get loans and sum amounts. Return borrowers where total loan amount exceeds 10x monthly income, sorted by ratio descending.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name", "income_threshold": "Max monthly income"},
    },
    {
        "title": "Find borrowers with no guarantors",
        "spec": "Get borrowers in a city (limit 5). For each, check guarantors. Assign result as list of borrower dicts who have zero guarantors.",
        "host_functions": ["query_borrowers_by_city", "query_guarantors"],
        "input_schema": {"city": "City name"},
    },

    # --- BATCH 4: Aggregation patterns ---
    {
        "title": "Total outstanding by city",
        "spec": "Given list of cities, get portfolio summary for each. Return dict mapping city to total_outstanding, plus grand_total across all cities.",
        "host_functions": ["query_portfolio_summary"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "Average loan tenor by product type",
        "spec": "Get borrowers in a city, then loans. Group by product. Compute average tenor_months per product. Return dict mapping product to average tenor.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Max DPD per borrower in city",
        "spec": "Get borrowers in a city, then loans for each. Find the maximum dpd across all loans for each borrower. Return list of dicts with borrower_name and max_dpd, sorted by max_dpd descending.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Sum guarantee amounts by relationship type",
        "spec": "Given borrower name, get borrower then guarantors. Group guarantors by relationship field. Sum guarantee_amount per relationship. Return dict mapping relationship to total guarantee.",
        "host_functions": ["query_borrower", "query_guarantors"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Total collateral value per loan",
        "spec": "Given borrower name, get loans, then collateral for each loan. Sum appraised_value per loan. Return list of dicts with loan_id and total_collateral_value.",
        "host_functions": ["query_borrower", "query_loans", "query_collateral"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Transaction volume by type",
        "spec": "Given borrower name, get transactions. Group by transaction type. For each type compute count and total amount. Return dict mapping type to dict with count and total_amount.",
        "host_functions": ["query_borrower", "query_transactions"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Count borrowers per credit score band",
        "spec": "Get borrowers in a city. Bucket credit scores into bands: poor (<500), fair (500-649), good (650-749), excellent (750+). Return dict mapping band to count.",
        "host_functions": ["query_borrowers_by_city"],
        "input_schema": {"city": "City name", "limit": "Max borrowers"},
    },

    # --- BATCH 5: Multi-hop joins ---
    {
        "title": "Borrower full profile with all data",
        "spec": "Given borrower name from inputs: call query_borrower with {'name': inputs['borrower_name']}. Get loans with query_loans using borrower_id. Get guarantors with query_guarantors using borrower_id. For each loan, call query_payments and query_collateral with loan_id. Assign result = {'profile': borrower, 'loans': loans, 'guarantors': guarantors, 'payments': payments_dict, 'collateral': collateral_dict} where payments_dict and collateral_dict map loan_id to list.",
        "host_functions": ["query_borrower", "query_loans", "query_guarantors", "query_payments", "query_collateral"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Loan risk assessment",
        "spec": "Given loan_id, get loan details using query_loan_details. The result has a 'loan' sub-dict with borrower_id. Look up borrower_id in the loan dict to find the borrower_id string. Note: you cannot call query_borrower with a borrower_id, only with a name. So skip borrower lookup. Get guarantors using the borrower_id. Compute risk_score: 'high' if loan dpd>90 or status is 'defaulted', 'medium' if dpd>30, 'low' otherwise. Assign result as dict with loan_details, guarantor_count, risk_score.",
        "host_functions": ["query_loan_details", "query_guarantors"],
        "input_schema": {"loan_id": "Loan ID"},
    },
    {
        "title": "City lending activity report",
        "spec": "Given city: get portfolio summary, borrowers, and loans for each borrower. Compute: total_active_loans (status active), avg_amount of active loans, highest_dpd. Return report dict combining portfolio summary with computed metrics.",
        "host_functions": ["query_portfolio_summary", "query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Borrower payment and collection summary",
        "spec": "Given borrower name, get loans. For each loan get both payments and collection records. Compute per-loan: total_paid (sum of completed payment amounts), collection_actions_count. Assign result as list of loan summary dicts.",
        "host_functions": ["query_borrower", "query_loans", "query_payments", "query_collection_records"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Compare two borrowers side by side",
        "spec": "Given two borrower names, get profile and loans for each. Compute total_debt and loan_count for each. Return comparison dict with both borrowers' key metrics.",
        "host_functions": ["query_borrower", "query_loans"],
        "input_schema": {"borrower_name_1": "First borrower name", "borrower_name_2": "Second borrower name"},
    },

    # --- BATCH 6: Sorting and ranking ---
    {
        "title": "Rank cities by average loan amount",
        "spec": "Given list of cities, get portfolio summary for each. Sort by avg_loan_amount descending. Return ranked list with city and avg_loan_amount.",
        "host_functions": ["query_portfolio_summary"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "Top borrowers by total outstanding in city",
        "spec": "Get borrowers in a city. Sort by total_outstanding descending. Return top N as list of dicts with name and total_outstanding.",
        "host_functions": ["query_borrowers_by_city"],
        "input_schema": {"city": "City name", "top_n": "Number of top borrowers"},
    },
    {
        "title": "Largest loan for each borrower in city",
        "spec": "Get borrowers in a city, then loans for each. Find the largest loan (by amount) per borrower. Return list sorted by amount descending with borrower_name, loan_id, amount.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Rank payment methods by total volume",
        "spec": "Given borrower name, get loans, then payments for each. Aggregate total amount by payment method across all loans. Sort methods by total amount descending. Return ranked list.",
        "host_functions": ["query_borrower", "query_loans", "query_payments"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Most common collection outcome",
        "spec": "Given borrower name, get loans, then collection records for each. Count occurrences of each outcome. Return dict mapping outcome to count, plus most_common outcome.",
        "host_functions": ["query_borrower", "query_loans", "query_collection_records"],
        "input_schema": {"borrower_name": "Borrower name"},
    },

    # --- BATCH 7: Statistics and correlation ---
    {
        "title": "Loan amount distribution stats",
        "spec": "Get borrowers in a city, then loans. Collect all loan amounts. Use compute_statistics to get descriptive stats. Return the statistics dict.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "compute_statistics"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Income vs total loans correlation",
        "spec": "Get borrowers in a city. Extract monthly_income and total_loans for each. Use compute_correlation to find relationship. Return correlation results.",
        "host_functions": ["query_borrowers_by_city", "compute_correlation"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Payment amount statistics per loan",
        "spec": "Given borrower name, get loans, then payments for each. For each loan, collect payment amounts and use compute_statistics. Return dict mapping loan_id to its payment stats.",
        "host_functions": ["query_borrower", "query_loans", "query_payments", "compute_statistics"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Credit score vs outstanding correlation across cities",
        "spec": "Given list of cities, get borrowers in each. Combine all borrowers. Extract credit_score and total_outstanding. Use compute_correlation. Return correlation result plus count of borrowers analyzed.",
        "host_functions": ["query_borrowers_by_city", "compute_correlation"],
        "input_schema": {"cities": "Comma-separated cities"},
    },

    # --- BATCH 8: Pandas bridge patterns ---
    {
        "title": "Aggregate payments by method and status",
        "spec": "Given loan_id from inputs, get payments using query_payments. Use aggregate_data to group by 'method', with aggregations {'amount': 'sum'}. Assign result = aggregate_data(...)  at module level.",
        "host_functions": ["query_payments", "aggregate_data"],
        "input_schema": {"loan_id": "Loan ID"},
    },
    {
        "title": "Tabulate and sort loans for borrower",
        "spec": "Given borrower name, get loans. Use tabulate_data to sort by amount descending and select columns: loan_id, product, amount, status, dpd. Return sorted records.",
        "host_functions": ["query_borrower", "query_loans", "tabulate_data"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Pivot collection outcomes by action type",
        "spec": "Given borrower name, get loans, then collection records for all loans. Build flat records with action_type and outcome fields. Use pivot_data with action_type as index, outcome as columns, counting occurrences. Return pivot result.",
        "host_functions": ["query_borrower", "query_loans", "query_collection_records", "pivot_data"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Aggregate borrowers by employment status",
        "spec": "Get borrowers in a city. Build records list. Use aggregate_data to group by employment_status... wait, query_borrowers_by_city doesn't return employment_status. Instead group by credit score band. Bucket credit_score into bands (poor/fair/good/excellent), add band field to each record, use aggregate_data to group by band computing mean of monthly_income. Return result.",
        "host_functions": ["query_borrowers_by_city", "aggregate_data"],
        "input_schema": {"city": "City name"},
    },

    # --- BATCH 9: Interactive patterns ---
    {
        "title": "Interactive borrower search",
        "spec": "Ask user for borrower name using ask_user. Look up borrower using query_borrower. Assign result as the borrower dict. Do not use dir() or type inspection.",
        "host_functions": ["ask_user", "query_borrower"],
        "input_schema": {},
    },
    {
        "title": "Interactive loan detail viewer",
        "spec": "Ask user for loan ID using ask_user. Get loan details. Ask user if they want payment details using ask_confirm. If yes, include payments in result. Assign result as dict.",
        "host_functions": ["ask_user", "ask_confirm", "query_loan_details"],
        "input_schema": {},
    },
    {
        "title": "City borrower browser with limit",
        "spec": "Present city choices using ask_choice with options Jakarta, Surabaya, Bandung. Ask for number of results using ask_number with min 1 max 50. Get borrowers for chosen city with chosen limit. Assign result as dict with selected_city, limit, and borrowers list.",
        "host_functions": ["ask_choice", "ask_number", "query_borrowers_by_city"],
        "input_schema": {},
    },
    {
        "title": "Guided portfolio analysis",
        "spec": "Ask user to choose analysis type using ask_choice: options are 'summary', 'delinquency'. Ask for city using ask_user. If summary: get portfolio summary. If delinquency: get delinquency stats. Assign result as dict with analysis_type and data.",
        "host_functions": ["ask_choice", "ask_user", "query_portfolio_summary", "query_delinquency_stats"],
        "input_schema": {},
    },

    # --- BATCH 10: Complex multi-step ---
    {
        "title": "Borrower risk categorization",
        "spec": "Get borrowers in a city, then loans for each. Categorize each borrower: high_risk if any loan dpd > 90 or any defaulted, medium_risk if any dpd > 30, low_risk otherwise. Return dict mapping risk category to list of borrower names.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Weighted average interest rate for city",
        "spec": "Get borrowers in a city, then loans. Compute weighted average interest rate where weight is loan amount. Formula: sum(rate * amount) / sum(amount). Return dict with weighted_avg_rate and total_loan_count.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Payment completion timeline for loan",
        "spec": "Given loan_id, get payments. Sort by payment_date. Compute running_total of completed payment amounts. Return list of dicts with payment_date, amount, running_total.",
        "host_functions": ["query_payments"],
        "input_schema": {"loan_id": "Loan ID"},
    },
    {
        "title": "Borrower employment income comparison",
        "spec": "Get borrowers in a city. Group by employment_status field (from query_borrower for each). Wait - query_borrowers_by_city doesn't have employment_status. Instead: get borrowers in city, for first 5 get full profile via query_borrower using their name. Group by employment_status. Compute avg monthly_income per status. Return dict.",
        "host_functions": ["query_borrowers_by_city", "query_borrower"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Collateral type distribution for city",
        "spec": "Get borrowers in a city (limit 5), loans for each, collateral for each loan. Count collateral items by type field. Return dict mapping collateral type to count.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "query_collateral"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Loan product popularity ranking",
        "spec": "Get borrowers in a city, then loans. Count loans per product type. Also compute total amount per product. Assign result as list of dicts with product, loan_count, total_amount sorted by loan_count descending. Use simple dict-based counting, no sets.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Multi-city borrower count comparison",
        "spec": "Given comma-separated cities, get portfolio summary for each. Return list of dicts with city and total_borrowers, sorted by total_borrowers descending.",
        "host_functions": ["query_portfolio_summary"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "Borrower transaction channel preference",
        "spec": "Given borrower name, get transactions. Find most-used channel (by count). Return dict with preferred_channel, usage_count, and all channel counts.",
        "host_functions": ["query_borrower", "query_transactions"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Loan disbursement amount percentiles",
        "spec": "Get borrowers in a city, then loans. Collect all loan amounts. Use compute_statistics to get percentiles (p25, p75, p90). Return dict with the percentile values and loan count.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "compute_statistics"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Guarantor income adequacy check",
        "spec": "Given borrower name, get loans and guarantors. For each guarantor, check if their monthly_income is at least 30% of their guarantee_amount. Return list of guarantors with name, monthly_income, guarantee_amount, and adequate (bool).",
        "host_functions": ["query_borrower", "query_guarantors"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Network degree analysis of borrower-guarantor graph",
        "spec": "Get borrowers in a city (limit 5), guarantors for each. Build edge list. Use analyze_network with analysis='degree'. Return degree results showing which nodes have most connections.",
        "host_functions": ["query_borrowers_by_city", "query_guarantors", "analyze_network"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Delinquency trend across severity buckets",
        "spec": "Get all delinquency stats (bucket=None). Each bucket dict has keys: bucket, loan_count, total_outstanding, avg_dpd, recovery_rate. Sum total_outstanding across all buckets. For each bucket compute share_pct = bucket's total_outstanding / overall total * 100. Assign result as list of dicts with bucket, share_pct, loan_count.",
        "host_functions": ["query_delinquency_stats"],
        "input_schema": {},
    },
    {
        "title": "Find borrowers with multiple loan products",
        "spec": "Get borrowers in a city, then loans for each. Find borrowers who have loans in more than one product type. Return list of dicts with borrower_name and set of product types they hold.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Collection escalation rate",
        "spec": "Given borrower name, get loans, then collection records for each. Count total records and count where outcome is 'escalated'. Compute escalation_rate = escalated / total. Return dict with total_actions, escalated_count, escalation_rate.",
        "host_functions": ["query_borrower", "query_loans", "query_collection_records"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Tabulate delinquency stats sorted by recovery",
        "spec": "Get delinquency stats for all buckets. Use tabulate_data to sort by recovery_rate ascending and select columns: bucket, loan_count, recovery_rate. Return sorted table.",
        "host_functions": ["query_delinquency_stats", "tabulate_data"],
        "input_schema": {},
    },
    {
        "title": "Aggregate transactions by channel and type",
        "spec": "Given borrower name, get transactions. Use aggregate_data to group by channel, computing sum of amount and count. Return aggregated result.",
        "host_functions": ["query_borrower", "query_transactions", "aggregate_data"],
        "input_schema": {"borrower_name": "Borrower name"},
    },

    # --- BATCH 11: Multi-city loops (weak pattern) ---
    {
        "title": "Compare total outstanding across cities",
        "spec": "Given comma-separated cities in inputs['cities'], split by comma. For each city call query_portfolio_summary({'city': city}). Collect total_outstanding from each result. Return dict mapping city to total_outstanding plus a grand_total key with sum.",
        "host_functions": ["query_portfolio_summary"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "Average credit score per city",
        "spec": "Given comma-separated cities, for each city get borrowers (limit 10). Compute average credit_score per city. Return dict mapping city to avg_credit_score.",
        "host_functions": ["query_borrowers_by_city"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "NPL ratio comparison across cities",
        "spec": "Given comma-separated cities, get portfolio summary for each. Return list of dicts with city and npl_ratio, sorted by npl_ratio descending.",
        "host_functions": ["query_portfolio_summary"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "Total loans and average DPD per city",
        "spec": "Given comma-separated cities. For each city: get borrowers (limit 5), then loans for each borrower. Count total loans and compute average DPD across all loans in that city. Return list of dicts with city, total_loans, avg_dpd.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "City with highest average loan amount",
        "spec": "Given comma-separated cities. For each city get portfolio summary. Find city with highest avg_loan_amount. Return dict with best_city, avg_loan_amount, and all_cities list of dicts.",
        "host_functions": ["query_portfolio_summary"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "Cross-city delinquency comparison",
        "spec": "Given comma-separated cities. For each city get borrowers (limit 5), then loans. Count loans with dpd > 0 and total loans. Compute delinquency_rate = delinquent / total. Return list of dicts with city, delinquent_count, total_loans, delinquency_rate sorted by rate descending.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"cities": "Comma-separated city names"},
    },

    # --- BATCH 12: Correct host function selection (collateral vs guarantor confusion) ---
    {
        "title": "Total collateral value for borrower",
        "spec": "Given borrower name, get borrower profile, then loans, then for EACH loan call query_collateral({'loan_id': loan['loan_id']}). Sum all appraised_value across all collateral items. Return dict with borrower_name, total_collateral_value, collateral_count.",
        "host_functions": ["query_borrower", "query_loans", "query_collateral"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Collateral summary per loan",
        "spec": "Given borrower name, get borrower then loans. For each loan call query_collateral with loan_id. Return list of dicts with loan_id, loan_amount, collateral_items (count), total_collateral_value (sum of appraised_value), coverage_ratio (total_collateral / loan_amount).",
        "host_functions": ["query_borrower", "query_loans", "query_collateral"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Collateral type breakdown for city",
        "spec": "Get borrowers in city (limit 5), loans for each, collateral for each loan. Group all collateral by type field. For each type compute count and total_value (sum of appraised_value). Return dict mapping type to dict with count and total_value.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "query_collateral"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Guarantor list for borrower",
        "spec": "Given borrower name, get borrower profile using query_borrower. Then call query_guarantors({'borrower_id': borrower['borrower_id']}). Return list of guarantor dicts.",
        "host_functions": ["query_borrower", "query_guarantors"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Guarantor total exposure for city",
        "spec": "Get borrowers in city (limit 5). For each borrower call query_guarantors with borrower_id. Collect all guarantors. Group by guarantor name, sum guarantee_amount. Return list of dicts with guarantor_name and total_exposure sorted descending.",
        "host_functions": ["query_borrowers_by_city", "query_guarantors"],
        "input_schema": {"city": "City name"},
    },

    # --- BATCH 13: Filter then aggregate (weak pattern) ---
    {
        "title": "Total amount of active loans in city",
        "spec": "Get borrowers in city (limit 5), loans for each. Filter to loans where status == 'active'. Sum their amounts. Return dict with city, active_loan_count, total_active_amount.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Average payment amount for completed payments",
        "spec": "Given borrower name, get borrower, loans, payments for each loan. Filter payments where status == 'completed'. Compute average amount of completed payments. Return dict with total_completed, avg_amount.",
        "host_functions": ["query_borrower", "query_loans", "query_payments"],
        "input_schema": {"borrower_name": "Borrower name"},
    },
    {
        "title": "Count defaulted loans per city",
        "spec": "Given comma-separated cities. For each city get borrowers (limit 5), then loans. Count loans where status == 'defaulted'. Return dict mapping city to defaulted_count.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "High DPD loans with collateral coverage",
        "spec": "Get borrowers in city (limit 5), loans for each. Filter loans where dpd > int(inputs['min_dpd']). For each filtered loan get collateral. Compute coverage = sum(collateral appraised_value) / loan amount. Return list of dicts with loan_id, dpd, amount, collateral_value, coverage_ratio.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "query_collateral"],
        "input_schema": {"city": "City name", "min_dpd": "Minimum DPD threshold"},
    },
    {
        "title": "Restructured loans total by city",
        "spec": "Given comma-separated cities. For each city get borrowers (limit 5), then loans. Filter loans where status == 'restructured'. Sum amounts. Return dict mapping city to dict with count and total_amount.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"cities": "Comma-separated city names"},
    },

    # --- BATCH 14: Computed ranking (weak pattern) ---
    {
        "title": "Rank borrowers by debt to income ratio",
        "spec": "Get borrowers in city. For each borrower get loans. Compute total_debt = sum of loan amounts for active loans. Compute ratio = total_debt / monthly_income. Return top N borrowers by ratio descending, as list of dicts with name, total_debt, monthly_income, ratio.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name", "top_n": "Number of top results"},
    },
    {
        "title": "Rank cities by total loan volume",
        "spec": "Given comma-separated cities. For each get portfolio summary. Sort by total_loans descending. Return sorted list of dicts with city and total_loans.",
        "host_functions": ["query_portfolio_summary"],
        "input_schema": {"cities": "Comma-separated city names"},
    },
    {
        "title": "Top loans by remaining balance",
        "spec": "Get borrowers in city (limit 5), loans for each. For each loan get payments. Compute total_paid = sum of completed payment amounts. Compute remaining = loan amount - total_paid. Return top N loans by remaining descending as list of dicts with loan_id, amount, total_paid, remaining.",
        "host_functions": ["query_borrowers_by_city", "query_loans", "query_payments"],
        "input_schema": {"city": "City name", "top_n": "Number of results"},
    },
    {
        "title": "Borrowers sorted by number of loans",
        "spec": "Get borrowers in city. For each get loans. Count loans per borrower. Sort by loan_count descending. Return list of dicts with borrower_name and loan_count.",
        "host_functions": ["query_borrowers_by_city", "query_loans"],
        "input_schema": {"city": "City name"},
    },
    {
        "title": "Rank guarantors by average guarantee amount",
        "spec": "Get borrowers in city (limit 5). For each get guarantors. Group all guarantors by name. Compute average guarantee_amount per guarantor. Return top N by avg_amount descending.",
        "host_functions": ["query_borrowers_by_city", "query_guarantors"],
        "input_schema": {"city": "City name", "top_n": "Number of top guarantors"},
    },
]

# ---------------------------------------------------------------------------
# Pipeline prompt (same as orchestrator's pipeline agent)
# ---------------------------------------------------------------------------

PIPELINE_SYSTEM = f"""You are a TDD code generation pipeline. Given a spec and host functions, produce ALL of the following in ONE response using exact section markers.

{MONTY_LIMITATIONS}

## Response format

===RESEARCH===
Analyze: 1) OBJECTIVE 2) DATA NEEDED 3) HOST FUNCTIONS TO USE 4) EDGE CASES. Be concise.

===TESTS===
Write assert statements that validate `result` variable. No imports, no test frameworks. Only assert statements.

===CODE===
Write Monty sandbox Python code. Assign final output to `result`. Read parameters from `inputs` dict.
NEVER redefine host functions. NEVER hardcode values. NEVER create mock data.

===REVIEW===
One paragraph: does output match the requirement? Plain language, no code.

CRITICAL: Use exact markers ===RESEARCH===, ===TESTS===, ===CODE===, ===REVIEW===. Each section must be present."""


def _parse_pipeline_response(text: str) -> dict[str, str]:
    sections = {}
    for key in ("RESEARCH", "TESTS", "CODE", "REVIEW"):
        marker = f"==={key}==="
        start = text.find(marker)
        if start == -1:
            continue
        start += len(marker)
        next_markers = [text.find(f"==={k}===", start) for k in ("RESEARCH", "TESTS", "CODE", "REVIEW") if text.find(f"==={k}===", start) > start]
        end = min(next_markers) if next_markers else len(text)
        sections[key.lower()] = text[start:end].strip()
    return sections


def _clean_llm_code(text: str) -> str:
    m = re.search(r'```(?:python)?\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    lines = text.strip().split("\n")
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and (stripped.startswith(("import ", "from ", "def ", "class ", "result", "#"))
                         or re.match(r'^[a-z_]\w*\s*=', stripped)
                         or stripped.startswith("assert ")):
            start = i
            break
    end = len(lines)
    for i in range(len(lines) - 1, start, -1):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith(("#", "assert ", "result")) or re.match(r'^[a-z_)\]\}]', stripped):
            end = i + 1
            break
    return "\n".join(lines[start:end]).strip()


def _syntax_check(code: str) -> str | None:
    try:
        compile(code, "<check>", "exec")
        return None
    except SyntaxError as e:
        return f"line {e.lineno}: {e.msg}"


def _fn_docs(allowlist: list[str]) -> str:
    return "\n".join(
        f"- {HOST_FUNCTION_DESCRIPTIONS[f]}" for f in allowlist
        if f in HOST_FUNCTION_DESCRIPTIONS
    )


async def generate_one(task: dict, agent) -> dict | None:
    """Generate one training triple. Returns dict or None on failure."""
    fn_doc = _fn_docs(task["host_functions"])
    schema = task.get("input_schema", {})
    schema_doc = "\n".join(f"  - {k}: {v}" for k, v in schema.items())

    prompt = f"Spec:\n{task['spec']}\n\nHost functions:\n{fn_doc}"
    if schema_doc:
        prompt += f"\n\nInput parameters in `inputs` dict:\n{schema_doc}"
    prompt += "\n\nGenerate all sections: RESEARCH, TESTS, CODE, REVIEW."

    try:
        r = await agent.run(prompt)
    except Exception as e:
        print(f"  LLM error: {e}")
        return None

    sections = _parse_pipeline_response(r.output)
    if not sections.get("code") or not sections.get("tests"):
        print(f"  Missing sections: {list(sections.keys())}")
        return None

    code = _clean_llm_code(sections["code"])
    test_code = _clean_llm_code(sections["tests"])

    # Syntax check
    err = _syntax_check(code)
    if err:
        print(f"  Syntax error in code: {err}")
        return None
    err = _syntax_check(test_code)
    if err:
        print(f"  Syntax error in tests: {err}")
        return None

    # Verify result assignment
    if not re.search(r'^result\s*=', code, re.MULTILINE):
        print(f"  No result assignment")
        return None

    # Verify uses host functions
    called = [fn for fn in task["host_functions"] if fn + "(" in code]
    if not called:
        print(f"  No host functions called")
        return None

    # Build mock inputs for test run
    mock_inputs = {}
    for k, desc in schema.items():
        dl = desc.lower()
        kl = k.lower()
        if "cities" in kl or "cities" in dl or "list" in dl:
            mock_inputs[k] = "Jakarta,Surabaya,Bandung"
        elif "amount" in kl:
            mock_inputs[k] = 1000000
        elif "dpd" in kl or "threshold" in kl or "limit" in kl or "top" in kl or "score" in kl:
            mock_inputs[k] = 10
        elif "id" in kl and "city" not in kl:
            mock_inputs[k] = "TEST-001"
        elif "date" in kl:
            mock_inputs[k] = "2025-01-01"
        elif "city" in kl or "city" in dl:
            mock_inputs[k] = "Jakarta"
        elif "name" in kl or "name" in dl:
            mock_inputs[k] = "Jakarta"
        elif "bucket" in kl:
            mock_inputs[k] = "31-60"
        elif "status" in kl or "type" in kl:
            mock_inputs[k] = "completed"
        else:
            mock_inputs[k] = "test"

    # Run tests
    test_result = run_tests(code, test_code, task["host_functions"], inputs=mock_inputs)
    if not test_result.passed:
        print(f"  Tests failed: {test_result.failures[0][:100] if test_result.failures else 'unknown'}")
        return None

    return {
        "title": task["title"],
        "spec": task["spec"],
        "host_functions": task["host_functions"],
        "input_schema": schema,
        "research": sections.get("research", ""),
        "tests": test_code,
        "code": code,
        "review": sections.get("review", ""),
    }


async def main():
    import os
    os.environ.setdefault("OPENAI_API_KEY", "not-needed")
    os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:8081/v1")
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.settings import ModelSettings

    api_key = os.environ.get("CODE_FACTORY_DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: CODE_FACTORY_DEEPSEEK_API_KEY not set")
        sys.exit(1)

    model = OpenAIChatModel(
        "deepseek-chat",
        provider=OpenAIProvider(base_url="https://api.deepseek.com", api_key=api_key),
    )

    agent = Agent(
        model,
        output_type=str,
        model_settings=ModelSettings(max_tokens=16384),
        instructions=PIPELINE_SYSTEM,
        name="training-pipeline",
    )

    out_dir = Path(__file__).parent.parent / "training_data"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "triples.json"

    # Load existing results to skip already-passed tasks
    existing = []
    existing_titles = set()
    if out_file.exists():
        with open(out_file) as f:
            existing = json.load(f)
        existing_titles = {t["title"] for t in existing}

    results = list(existing)
    new_ok = 0
    new_skip = 0
    for i, task in enumerate(TASKS):
        if task["title"] in existing_titles:
            print(f"\n[{i+1}/{len(TASKS)}] {task['title']} — cached")
            continue
        print(f"\n[{i+1}/{len(TASKS)}] {task['title']}")
        triple = await generate_one(task, agent)
        if triple:
            print(f"  OK ({len(triple['code'])} chars, {triple['tests'].count('assert ')} asserts)")
            results.append(triple)
            new_ok += 1
        else:
            print(f"  SKIP")
            new_skip += 1

    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Total: {len(results)} triples ({len(existing)} cached + {new_ok} new, {new_skip} failed)")
    print(f"Saved to {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
