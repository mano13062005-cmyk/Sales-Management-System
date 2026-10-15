SALES INTELLIGENCE HUB — SEARCH + CALCULATION CHECK UPDATE

YOUR DATABASE IS ALREADY WORKING. DO NOT RERUN THE SQL SCRIPT OR CSV IMPORTER.

1. Extract this ZIP.
2. In your existing SalesHub_Fixed folder, rename app.py to app_old.py.
3. Copy the NEW app.py from this ZIP into the existing SalesHub_Fixed folder.
4. In VS Code terminal, stop the old app with Ctrl+C.
5. Run: python -m streamlit run app.py
6. Log in and open "Calculation Check" to identify any mismatch.
7. Open "Search Records" to search sales and payments by ID, customer, mobile, branch, product, method, or date.

This update does NOT change any MySQL data. The included schema and importer are for fresh installation only.

ORIGINAL CSV BASELINE (before adding new sales or payments):
Gross: INR 36,768,000.00
Payment ledger: INR 19,028,113.00
Pending: INR 17,739,887.00

The original sales CSV has received_amount=0 in every row; payments must be calculated from payment_splits.
If new sales or payments have been added, the live totals will differ from the original CSV baseline.

The Calculation Check page compares stored amounts with payment ledger totals, and shows discrepancies without automatically changing data. Python syntax checked; live MySQL connection not available in this environment.
