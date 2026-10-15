"""Import the user's four CSVs into sales_management_system after running schema.sql.

WARNING: sales_management_system.sql recreates the four tables. This importer
refuses to run unless all four tables are empty, and never deletes rows.
Passwords in users(1).csv are hashed with SHA-256 for compatibility with app.py.
For real production use, upgrade to a salted password hash (e.g. Argon2/bcrypt).
"""
import csv
import hashlib
import os
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import mysql.connector

ROOT = Path(__file__).resolve().parent
DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "root"),
    "database": "sales_management_system",
}
FILES = {
    "branches": "branches(1).csv",
    "users": "users(1).csv",
    "customer_sales": "customer_sales.csv",
    "payment_splits": "payment_splits(1).csv",
}
REQUIRED = {
    "branches": {"branch_id", "branch_name", "branch_admin_name"},
    "users": {"user_id", "username", "password", "branch_id", "role", "email"},
    "customer_sales": {"sale_id", "branch_id", "date", "name", "mobile_number", "product_name", "gross_sales", "received_amount", "status"},
    "payment_splits": {"payment_id", "sale_id", "payment_date", "amount_paid", "payment_method"},
}


def read_csv(name):
    path = ROOT / FILES[name]
    if not path.exists():
        raise ValueError(f"Missing file: {path.name}. Copy it beside import_csv_data.py")
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not REQUIRED[name].issubset(set(reader.fieldnames or [])):
            raise ValueError(f"Unexpected columns in {path.name}: {reader.fieldnames}")
        return list(reader)


def main():
    branches = read_csv("branches")
    users = read_csv("users")
    sales = read_csv("customer_sales")
    payments = read_csv("payment_splits")

    for name, rows, key in [("branches", branches, "branch_id"),
                            ("users", users, "user_id"),
                            ("customer_sales", sales, "sale_id"),
                            ("payment_splits", payments, "payment_id")]:
        ids = [r[key] for r in rows]
        if len(ids) != len(set(ids)) or any(not x for x in ids):
            raise ValueError(f"Missing/duplicate {key} in {FILES[name]}")

    branch_ids = {r["branch_id"] for r in branches}
    sale_ids = {r["sale_id"] for r in sales}
    if any(r["branch_id"] not in branch_ids for r in sales):
        raise ValueError("customer_sales has invalid branch IDs")
    if any(r["branch_id"] and r["branch_id"] not in branch_ids for r in users):
        raise ValueError("users has invalid branch IDs")
    if any(r["sale_id"] not in sale_ids for r in payments):
        raise ValueError("payment_splits has invalid sale IDs")
    mobile_numbers = [r["mobile_number"].strip() for r in sales]
    if len(mobile_numbers) != len(set(mobile_numbers)):
        raise ValueError("Duplicate mobile numbers conflict with the SQL UNIQUE constraint")
    gross = {r["sale_id"]: Decimal(r["gross_sales"]) for r in sales}
    paid = defaultdict(lambda: Decimal("0"))
    for row in payments:
        amount = Decimal(row["amount_paid"])
        if amount <= 0:
            raise ValueError("Payment amounts must be positive")
        paid[row["sale_id"]] += amount
    overpaid = [sid for sid in sale_ids if paid[sid] > gross[sid]]
    if overpaid:
        raise ValueError(f"Payment totals exceed gross sales for {len(overpaid)} sales")

    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cursor = conn.cursor()
        try:
            counts = {}
            for table in FILES:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                counts[table] = cursor.fetchone()[0]
            if any(counts.values()):
                raise ValueError(f"Database tables are not empty: {counts}. Run sales_management_system.sql first; no data was modified.")
            # MySQL connector starts a transaction on the first statement by default.
            cursor.executemany(
                "INSERT INTO branches (branch_id, branch_name, branch_admin_name) VALUES (%s,%s,%s)",
                [(int(r["branch_id"]), r["branch_name"], r["branch_admin_name"]) for r in branches],
            )
            cursor.executemany(
                "INSERT INTO users (user_id, username, password, branch_id, role, email) VALUES (%s,%s,%s,%s,%s,%s)",
                [(int(r["user_id"]), r["username"], hashlib.sha256(r["password"].encode("utf-8")).hexdigest(),
                  int(r["branch_id"]) if r["branch_id"] else None, r["role"], r["email"])
                 for r in users],
            )
            cursor.executemany(
                """INSERT INTO customer_sales
                   (sale_id, branch_id, `date`, name, mobile_number, product_name,
                    gross_sales, received_amount, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,0,'Open')""",
                [(int(r["sale_id"]), int(r["branch_id"]), r["date"], r["name"],
                  r["mobile_number"].strip(), r["product_name"], Decimal(r["gross_sales"]))
                 for r in sales],
            )
            # AFTER INSERT trigger updates received_amount and status after each payment.
            cursor.executemany(
                """INSERT INTO payment_splits
                   (payment_id, sale_id, payment_date, amount_paid, payment_method)
                   VALUES (%s,%s,%s,%s,%s)""",
                [(int(r["payment_id"]), int(r["sale_id"]), r["payment_date"],
                  Decimal(r["amount_paid"]), r["payment_method"]) for r in payments],
            )
            cursor.execute("SELECT COUNT(*), COALESCE(SUM(received_amount),0) FROM customer_sales")
            n_sales, total_received = cursor.fetchone()
            cursor.execute("SELECT COALESCE(SUM(amount_paid),0) FROM payment_splits")
            total_payments = cursor.fetchone()[0]
            if n_sales != len(sales) or total_received != total_payments:
                raise ValueError("Payment reconciliation failed; transaction rolled back")
            conn.commit()
            print("Import successful:")
            print(f"  Branches: {len(branches)}")
            print(f"  Users: {len(users)}")
            print(f"  Sales: {len(sales)}")
            print(f"  Payments: {len(payments)}")
            print("  Received amounts and pending amounts match the payment records.")
            print("  Login with the original username and password from your users CSV.")
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
