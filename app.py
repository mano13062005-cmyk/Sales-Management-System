import hashlib
import os
from datetime import date

import mysql.connector
import pandas as pd
import streamlit as st
from mysql.connector import Error

st.set_page_config(
    page_title="Sales Intelligence Hub",
    page_icon="📊",
    layout="wide",
)

# Update these values to match your MySQL installation.
try:
    _mysql_secrets = st.secrets.get("mysql", {})
except Exception:
    _mysql_secrets = {}
DB_CONFIG = {
    "host": _mysql_secrets.get("host", os.getenv("MYSQL_HOST", "localhost")),
    "port": int(_mysql_secrets.get("port", os.getenv("MYSQL_PORT", "3306"))),
    "user": _mysql_secrets.get("user", os.getenv("MYSQL_USER", "root")),
    "password": _mysql_secrets.get("password", os.getenv("MYSQL_PASSWORD", "root")),
    "database": "sales_management_system",
}


def connect_db():
    return mysql.connector.connect(**DB_CONFIG)


def query_df(sql, params=()):
    conn = connect_db()
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        return pd.DataFrame(cursor.fetchall())
    finally:
        if cursor is not None:
            cursor.close()
        conn.close()


def execute_sql(sql, params=()):
    conn = connect_db()
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        return cursor.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        conn.close()


def password_hash(password):
    # Matches the SHA-256 hash stored in the users.password column.
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def authenticate(username, password):
    users = query_df(
        """
        SELECT user_id, username, password, role, branch_id
        FROM users
        WHERE username = %s
        LIMIT 1
        """,
        (username.strip(),),
    )
    if users.empty:
        return None

    row = users.iloc[0]
    stored = str(row["password"]).strip().lower()
    if stored != password_hash(password):
        return None

    branch_id = int(row["branch_id"]) if pd.notna(row["branch_id"]) else None
    return {
        "user_id": int(row["user_id"]),
        "username": str(row["username"]),
        "role": str(row["role"]),
        "branch_id": branch_id,
    }


def get_branches():
    return query_df(
        "SELECT branch_id, branch_name, branch_admin_name FROM branches ORDER BY branch_name"
    )


def sales_where(user, alias="cs"):
    if user["role"] == "Super Admin":
        return "", ()
    return f" WHERE {alias}.branch_id = %s", (user["branch_id"],)


def get_sales(user):
    where, params = sales_where(user, "cs")
    return query_df(
        f"""
        SELECT cs.sale_id, cs.date, cs.name AS customer_name, cs.mobile_number,
               cs.product_name, b.branch_name, cs.gross_sales, cs.received_amount,
               cs.pending_amount, cs.status
        FROM customer_sales cs
        JOIN branches b ON b.branch_id = cs.branch_id
        {where}
        ORDER BY cs.date DESC, cs.sale_id DESC
        """,
        params,
    )


def get_payments(user):
    where, params = sales_where(user, "cs")
    return query_df(
        f"""
        SELECT ps.payment_id, ps.sale_id, cs.name AS customer_name,
               b.branch_name, ps.payment_date, ps.amount_paid, ps.payment_method
        FROM payment_splits ps
        JOIN customer_sales cs ON cs.sale_id = ps.sale_id
        JOIN branches b ON b.branch_id = cs.branch_id
        {where}
        ORDER BY ps.payment_date DESC, ps.payment_id DESC
        """,
        params,
    )


def filter_search(df, search, columns):
    """Case-insensitive literal search; never interprets input as regex."""
    term = search.strip()
    if not term or df.empty:
        return df
    mask = pd.Series(False, index=df.index)
    for column in columns:
        if column in df.columns:
            mask = mask | df[column].astype(str).str.contains(
                term, case=False, regex=False, na=False
            )
    return df.loc[mask]


def reconciliation_df(user):
    """Read-only check against the payment ledger; no records are changed."""
    where, params = sales_where(user, "cs")
    return query_df(
        f"""
        SELECT cs.sale_id, cs.name AS customer_name, b.branch_name,
               cs.gross_sales, cs.received_amount AS stored_received,
               cs.pending_amount AS stored_pending,
               COALESCE(p.paid_total, 0) AS payments_total,
               cs.gross_sales - COALESCE(p.paid_total, 0) AS expected_pending,
               cs.received_amount - COALESCE(p.paid_total, 0) AS difference,
               cs.status AS stored_status,
               CASE WHEN COALESCE(p.paid_total, 0) >= cs.gross_sales
                    THEN 'Close' ELSE 'Open' END AS expected_status
        FROM customer_sales cs
        JOIN branches b ON b.branch_id = cs.branch_id
        LEFT JOIN (
            SELECT sale_id, SUM(amount_paid) AS paid_total
            FROM payment_splits GROUP BY sale_id
        ) p ON p.sale_id = cs.sale_id
        {where}
        ORDER BY cs.sale_id
        """,
        params,
    )


def reconciliation_page(user):
    st.title("Calculation Check")
    st.caption("Compares every sale's stored received amount with the sum of its payment records. Read-only: no database data is changed.")
    report = reconciliation_df(user)
    if report.empty:
        st.info("No sales available for this account.")
        return
    gross = sum(report["gross_sales"])
    stored = sum(report["stored_received"])
    paid = sum(report["payments_total"])
    pending = gross - paid
    cols = st.columns(4)
    cols[0].metric("Gross sales", f"₹{gross:,.2f}")
    cols[1].metric("Payments from ledger", f"₹{paid:,.2f}")
    cols[2].metric("Expected pending", f"₹{pending:,.2f}")
    cols[3].metric("Stored vs ledger difference", f"₹{stored - paid:,.2f}")
    st.caption("Formula: expected pending = gross sales − sum of payment_splits.amount_paid. Sales are grouped by sale date; payments have their own payment date.")
    mismatch = report.loc[
        report["difference"].map(lambda x: abs(float(x)) > 0.005)
        | (report["stored_status"] != report["expected_status"])
        | report.apply(lambda r: abs(float(r["stored_pending"]) - float(r["expected_pending"])) > 0.005, axis=1)
    ]
    if mismatch.empty:
        st.success("All visible sales match their payment records and expected status.")
    else:
        st.error(f"Found {len(mismatch)} sales with mismatched amounts or status. Nothing was modified.")
        st.dataframe(mismatch, use_container_width=True, hide_index=True)
        st.download_button("Download mismatches CSV", mismatch.to_csv(index=False).encode("utf-8"), "calculation_mismatches.csv", "text/csv")
        st.info("Do not re-import or reset your database. Review these rows before making corrections.")
    with st.expander("Show all sale calculations"):
        st.dataframe(report, use_container_width=True, hide_index=True)


def search_records_page(user):
    st.title("Search Records")
    term = st.text_input("Search sale ID, payment ID, customer, mobile, branch, product, or payment method", key="global_search")
    tab_sales, tab_payments = st.tabs(["Sales", "Payments"])
    with tab_sales:
        sales = filter_search(get_sales(user), term, ["sale_id", "customer_name", "mobile_number", "branch_name", "product_name", "status"])
        st.caption(f"{len(sales):,} matching sales")
        st.dataframe(sales, use_container_width=True, hide_index=True)
        st.download_button("Export sales results", sales.to_csv(index=False).encode("utf-8"), "search_sales.csv", "text/csv", key="search_sales_export")
    with tab_payments:
        payments = filter_search(get_payments(user), term, ["payment_id", "sale_id", "customer_name", "branch_name", "payment_method", "payment_date"])
        st.caption(f"{len(payments):,} matching payments")
        st.dataframe(payments, use_container_width=True, hide_index=True)
        st.download_button("Export payment results", payments.to_csv(index=False).encode("utf-8"), "search_payments.csv", "text/csv", key="search_payments_export")


def show_notice(page):
    """Show a one-time success message after Streamlit reruns."""
    notice = st.session_state.pop(f"success_{page}", None)
    if notice:
        st.success(notice)


def save_notice(page, message):
    st.session_state[f"success_{page}"] = message


def login_screen():
    st.title("📊 Sales Intelligence Hub")
    st.caption("Branch-wise sales, split payments, and financial reporting.")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", use_container_width=True)

    if submitted:
        if not username.strip() or not password:
            st.warning("Enter both username and password.")
            return
        try:
            user = authenticate(username, password)
            if user is None:
                st.error("Incorrect username or password.")
            elif user["role"] == "Admin" and user["branch_id"] is None:
                st.error("This Admin account has no assigned branch. Contact the Super Admin.")
            else:
                st.session_state["user"] = user
                st.rerun()
        except Error as exc:
            st.error(f"Database connection/query failed: {exc}")
            st.info("Check MySQL is running, DB_CONFIG is correct, and the SQL schema has been imported.")


def dashboard_page(user):
    st.title("Dashboard")
    where, params = sales_where(user, "cs")
    summary = query_df(
        f"""
        SELECT COUNT(*) AS total_sales,
               COALESCE(SUM(cs.gross_sales), 0) AS gross_sales,
               COALESCE(SUM(cs.received_amount), 0) AS received_amount,
               COALESCE(SUM(cs.pending_amount), 0) AS pending_amount
        FROM customer_sales cs
        {where}
        """,
        params,
    ).iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Sales", f"{int(summary['total_sales']):,}")
    c2.metric("Gross Sales", f"₹{float(summary['gross_sales']):,.2f}")
    c3.metric("Received", f"₹{float(summary['received_amount']):,.2f}")
    c4.metric("Pending", f"₹{float(summary['pending_amount']):,.2f}")

    if float(summary["gross_sales"]) > 0:
        pending_pct = float(summary["pending_amount"]) / float(summary["gross_sales"]) * 100
    else:
        pending_pct = 0.0
    st.metric("Pending Collection", f"{pending_pct:.2f}%")

    st.subheader("Branch-wise sales")
    if user["role"] == "Super Admin":
        branch_report = query_df(
            """
            SELECT b.branch_name, COUNT(cs.sale_id) AS total_sales,
                   COALESCE(SUM(cs.gross_sales), 0) AS gross_sales,
                   COALESCE(SUM(cs.received_amount), 0) AS received_amount,
                   COALESCE(SUM(cs.pending_amount), 0) AS pending_amount
            FROM branches b
            LEFT JOIN customer_sales cs ON cs.branch_id = b.branch_id
            GROUP BY b.branch_id, b.branch_name
            ORDER BY gross_sales DESC
            """
        )
    else:
        branch_report = query_df(
            """
            SELECT b.branch_name, COUNT(cs.sale_id) AS total_sales,
                   COALESCE(SUM(cs.gross_sales), 0) AS gross_sales,
                   COALESCE(SUM(cs.received_amount), 0) AS received_amount,
                   COALESCE(SUM(cs.pending_amount), 0) AS pending_amount
            FROM branches b
            LEFT JOIN customer_sales cs ON cs.branch_id = b.branch_id
            WHERE b.branch_id = %s
            GROUP BY b.branch_id, b.branch_name
            """,
            (user["branch_id"],),
        )
    st.dataframe(branch_report, use_container_width=True, hide_index=True)
    if not branch_report.empty:
        chart_data = branch_report[["branch_name", "gross_sales"]].copy()
        chart_data["gross_sales"] = pd.to_numeric(chart_data["gross_sales"])
        st.bar_chart(chart_data.set_index("branch_name"))

    st.subheader("Monthly sales trend (by sale date)")
    st.caption("This chart uses gross sales on each sale's date, not the dates when payments were collected.")
    trend_where, trend_params = sales_where(user, "cs")
    monthly = query_df(
        f"""
        SELECT DATE_FORMAT(cs.date, '%Y-%m') AS month,
               SUM(cs.gross_sales) AS gross_sales
        FROM customer_sales cs
        {trend_where}
        GROUP BY YEAR(cs.date), MONTH(cs.date), DATE_FORMAT(cs.date, '%Y-%m')
        ORDER BY YEAR(cs.date), MONTH(cs.date)
        """,
        trend_params,
    )
    if monthly.empty:
        st.info("No sales data available yet.")
    else:
        monthly["gross_sales"] = pd.to_numeric(monthly["gross_sales"])
        st.line_chart(monthly.set_index("month")["gross_sales"])

    st.subheader("Recent sales")
    sales = get_sales(user)
    if sales.empty:
        st.info("No sales records found.")
    else:
        st.dataframe(sales.head(10), use_container_width=True, hide_index=True)


def sales_page(user):
    st.title("Sales")
    show_notice("sales")
    branches = get_branches()

    with st.expander("Add a sale", expanded=False):
        if user["role"] == "Super Admin" and branches.empty:
            st.warning("Create a branch before adding a sale.")
        with st.form("add_sale_form"):
            customer_name = st.text_input("Customer name")
            mobile = st.text_input("Mobile number")
            product_name = st.selectbox("Product", ["DS", "DA", "BA", "FSD", "AI", "BI", "ML", "SQL", "Other"])
            sale_date = st.date_input("Sale date", value=date.today())
            gross_sales = st.number_input("Gross sales (₹)", min_value=0.01, step=500.0)
            selected_branch = None
            if user["role"] == "Super Admin":
                if not branches.empty:
                    selected_branch = st.selectbox(
                        "Branch",
                        branches.to_dict("records"),
                        format_func=lambda x: f"{x['branch_name']} (ID: {x['branch_id']})",
                    )
            else:
                st.text_input("Assigned branch", value=str(user["branch_id"]), disabled=True)

            submitted = st.form_submit_button("Save Sale")
            if submitted:
                if not customer_name.strip():
                    st.error("Customer name is required.")
                elif not mobile.strip():
                    st.error("Mobile number is required.")
                elif not mobile.strip().isdigit() or len(mobile.strip()) > 15:
                    st.error("Enter a mobile number containing only digits (up to 15 digits).")
                elif user["role"] == "Super Admin" and selected_branch is None:
                    st.error("Create/select a branch first.")
                else:
                    branch_id = (
                        int(selected_branch["branch_id"])
                        if user["role"] == "Super Admin"
                        else user["branch_id"]
                    )
                    try:
                        execute_sql(
                            """
                            INSERT INTO customer_sales
                                (branch_id, `date`, name, mobile_number, product_name,
                                 gross_sales, received_amount, status)
                            VALUES (%s, %s, %s, %s, %s, %s, 0, 'Open')
                            """,
                            (
                                branch_id, sale_date, customer_name.strip(),
                                mobile.strip(), product_name, gross_sales,
                            ),
                        )
                        save_notice("sales", "Sale added successfully. The sales report has been updated.")
                        st.rerun()
                    except mysql.connector.IntegrityError as exc:
                        if exc.errno == 1062:
                            st.error("This mobile number already exists. Enter an unused mobile number.")
                        else:
                            st.error(f"Could not add sale: {exc}")
                    except Error as exc:
                        st.error(f"Could not add sale: {exc}")

    st.subheader("Sales report")
    data = get_sales(user)
    search = st.text_input("Search by customer, mobile number, or product")
    data = filter_search(data, search, ["sale_id", "customer_name", "mobile_number", "product_name", "branch_name", "status"])
    st.caption(f"{len(data):,} matching sales")
    st.dataframe(data, use_container_width=True, hide_index=True)
    if not data.empty:
        st.download_button(
            "Download sales CSV",
            data=data.to_csv(index=False).encode("utf-8"),
            file_name="customer_sales.csv",
            mime="text/csv",
        )


def payments_page(user):
    st.title("Payment Splits")
    show_notice("payments")
    where, params = sales_where(user, "cs")
    eligible = query_df(
        f"""
        SELECT cs.sale_id, cs.name, cs.pending_amount
        FROM customer_sales cs
        {where + (' AND' if where else ' WHERE')} cs.pending_amount > 0
        ORDER BY cs.sale_id
        """,
        params,
    )

    with st.expander("Record a payment", expanded=False):
        if eligible.empty:
            st.info("There are no sales with a pending balance.")
        else:
            with st.form("add_payment_form"):
                sale = st.selectbox(
                    "Sale",
                    eligible.to_dict("records"),
                    format_func=lambda row: (
                        f"Sale #{row['sale_id']} — {row['name']} — "
                        f"pending ₹{float(row['pending_amount']):,.2f}"
                    ),
                )
                amount = st.number_input(
                    "Amount paid (₹)",
                    min_value=0.01,
                    max_value=float(sale["pending_amount"]),
                    value=min(500.0, float(sale["pending_amount"])),
                    step=0.01,
                )
                payment_date = st.date_input("Payment date", value=date.today())
                method = st.selectbox("Payment method", ["Cash", "UPI", "Card", "Bank Transfer", "Other"])
                submitted = st.form_submit_button("Save Payment")
                if submitted:
                    try:
                        execute_sql(
                            """
                            INSERT INTO payment_splits
                                (sale_id, payment_date, amount_paid, payment_method)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (int(sale["sale_id"]), payment_date, amount, method),
                        )
                        # The database trigger recalculates received_amount and status.
                        save_notice("payments", "Payment recorded successfully. Received and pending balances were updated.")
                        st.rerun()
                    except Error as exc:
                        st.error(f"Could not record payment: {exc}")

    st.subheader("Payment history")
    payments = get_payments(user)
    payment_search = st.text_input("Search payments by payment ID, sale ID, customer, branch, method, or date")
    payments = filter_search(payments, payment_search, ["payment_id", "sale_id", "customer_name", "branch_name", "payment_method", "payment_date"])
    st.caption(f"{len(payments):,} matching payments")
    st.dataframe(payments, use_container_width=True, hide_index=True)
    if not payments.empty:
        st.download_button(
            "Download payment CSV",
            data=payments.to_csv(index=False).encode("utf-8"),
            file_name="payment_splits.csv",
            mime="text/csv",
        )

    st.subheader("Collection by payment method")
    if user["role"] == "Super Admin":
        method_report = query_df(
            """
            SELECT ps.payment_method, SUM(ps.amount_paid) AS total_collected
            FROM payment_splits ps
            JOIN customer_sales cs ON cs.sale_id = ps.sale_id
            GROUP BY ps.payment_method
            ORDER BY total_collected DESC
            """
        )
    else:
        method_report = query_df(
            """
            SELECT ps.payment_method, SUM(ps.amount_paid) AS total_collected
            FROM payment_splits ps
            JOIN customer_sales cs ON cs.sale_id = ps.sale_id
            WHERE cs.branch_id = %s
            GROUP BY ps.payment_method
            ORDER BY total_collected DESC
            """,
            (user["branch_id"],),
        )
    st.dataframe(method_report, use_container_width=True, hide_index=True)


def branches_page():
    st.title("Branches")
    show_notice("branches")
    branches = query_df(
        """
        SELECT b.branch_id, b.branch_name, b.branch_admin_name,
               COUNT(cs.sale_id) AS total_sales,
               COALESCE(SUM(cs.gross_sales), 0) AS gross_sales,
               COALESCE(SUM(cs.received_amount), 0) AS received_amount,
               COALESCE(SUM(cs.pending_amount), 0) AS pending_amount
        FROM branches b
        LEFT JOIN customer_sales cs ON cs.branch_id = b.branch_id
        GROUP BY b.branch_id, b.branch_name, b.branch_admin_name
        ORDER BY b.branch_name
        """
    )
    st.dataframe(branches, use_container_width=True, hide_index=True)

    with st.expander("Add a branch"):
        with st.form("add_branch_form"):
            name = st.text_input("Branch name")
            admin_name = st.text_input("Branch administrator name")
            submitted = st.form_submit_button("Add Branch")
            if submitted:
                if not name.strip():
                    st.error("Branch name is required.")
                else:
                    try:
                        execute_sql(
                            "INSERT INTO branches (branch_name, branch_admin_name) VALUES (%s, %s)",
                            (name.strip(), admin_name.strip() or None),
                        )
                        save_notice("branches", "Branch added successfully.")
                        st.rerun()
                    except Error as exc:
                        st.error(f"Could not add branch: {exc}")

    st.subheader("Create an Admin login")
    branches_df = get_branches()
    if branches_df.empty:
        st.info("Add a branch before creating an Admin login.")
    else:
        with st.form("create_admin_form"):
            username = st.text_input("Admin username")
            email = st.text_input("Admin email")
            password = st.text_input("Temporary password", type="password")
            branch = st.selectbox(
                "Assigned branch",
                branches_df.to_dict("records"),
                format_func=lambda x: f"{x['branch_name']} (ID: {x['branch_id']})",
            )
            submitted = st.form_submit_button("Create Admin")
            if submitted:
                if not username.strip() or not email.strip() or not password:
                    st.error("Username, email, and password are required.")
                else:
                    try:
                        execute_sql(
                            """
                            INSERT INTO users (username, password, branch_id, role, email)
                            VALUES (%s, %s, %s, 'Admin', %s)
                            """,
                            (username.strip(), password_hash(password), int(branch["branch_id"]), email.strip()),
                        )
                        save_notice("branches", "Admin account created successfully. Share the temporary password securely.")
                        st.rerun()
                    except mysql.connector.IntegrityError as exc:
                        if exc.errno == 1062:
                            if "email" in str(exc).lower():
                                st.error("This email address is already registered. Please use a different email address.")
                            elif "username" in str(exc).lower():
                                st.error("This username is already taken. Please choose another username.")
                            else:
                                st.error("This Admin account conflicts with an existing record. Check username and email.")
                        else:
                            st.error(f"Could not create Admin: {exc}")
                    except Error as exc:
                        st.error(f"Could not create Admin: {exc}")


def sql_queries_page(user):
    st.title("SQL Analytical Queries")
    st.caption("Run the predefined read-only reports required by the project document.")
    options = {
        "All sales": """
            SELECT cs.*, b.branch_name FROM customer_sales cs
            JOIN branches b ON b.branch_id = cs.branch_id ORDER BY cs.sale_id DESC
        """,
        "Open sales": """
            SELECT cs.sale_id, cs.name, cs.product_name, cs.gross_sales, cs.pending_amount
            FROM customer_sales cs WHERE cs.status = 'Open'
        """,
        "Total gross sales by branch": """
            SELECT b.branch_name, COALESCE(SUM(cs.gross_sales), 0) AS total_gross_sales
            FROM branches b LEFT JOIN customer_sales cs ON cs.branch_id = b.branch_id
            GROUP BY b.branch_id, b.branch_name ORDER BY total_gross_sales DESC
        """,
        "Highest 3 sales": """
            SELECT sale_id, name, product_name, gross_sales
            FROM customer_sales ORDER BY gross_sales DESC LIMIT 3
        """,
        "Pending over ₹5000": """
            SELECT sale_id, name, gross_sales, received_amount, pending_amount
            FROM customer_sales WHERE pending_amount > 5000 ORDER BY pending_amount DESC
        """,
        "Monthly sales summary": """
            SELECT YEAR(`date`) AS sales_year, MONTH(`date`) AS sales_month,
                   SUM(gross_sales) AS gross_sales
            FROM customer_sales GROUP BY YEAR(`date`), MONTH(`date`)
            ORDER BY sales_year, sales_month
        """,
        "Payment method collections": """
            SELECT payment_method, SUM(amount_paid) AS total_collection
            FROM payment_splits GROUP BY payment_method ORDER BY total_collection DESC
        """,
    }
    selected = st.selectbox("Choose a report", list(options))
    if st.button("Run report"):
        try:
            sql = options[selected]
            if user["role"] != "Super Admin":
                # Branch-scoped alternatives prevent an Admin from seeing other branches.
                branch_id = user["branch_id"]
                scoped = {
                    "All sales": ("""
                        SELECT cs.*, b.branch_name FROM customer_sales cs
                        JOIN branches b ON b.branch_id = cs.branch_id
                        WHERE cs.branch_id = %s ORDER BY cs.sale_id DESC
                    """, (branch_id,)),
                    "Open sales": ("""
                        SELECT cs.sale_id, cs.name, cs.product_name, cs.gross_sales, cs.pending_amount
                        FROM customer_sales cs WHERE cs.status = 'Open' AND cs.branch_id = %s
                    """, (branch_id,)),
                    "Total gross sales by branch": ("""
                        SELECT b.branch_name, COALESCE(SUM(cs.gross_sales), 0) AS total_gross_sales
                        FROM branches b LEFT JOIN customer_sales cs ON cs.branch_id = b.branch_id
                        WHERE b.branch_id = %s GROUP BY b.branch_id, b.branch_name
                    """, (branch_id,)),
                    "Highest 3 sales": ("""
                        SELECT sale_id, name, product_name, gross_sales FROM customer_sales
                        WHERE branch_id = %s ORDER BY gross_sales DESC LIMIT 3
                    """, (branch_id,)),
                    "Pending over ₹5000": ("""
                        SELECT sale_id, name, gross_sales, received_amount, pending_amount
                        FROM customer_sales WHERE pending_amount > 5000 AND branch_id = %s
                        ORDER BY pending_amount DESC
                    """, (branch_id,)),
                    "Monthly sales summary": ("""
                        SELECT YEAR(`date`) AS sales_year, MONTH(`date`) AS sales_month,
                               SUM(gross_sales) AS gross_sales FROM customer_sales
                        WHERE branch_id = %s GROUP BY YEAR(`date`), MONTH(`date`)
                        ORDER BY sales_year, sales_month
                    """, (branch_id,)),
                    "Payment method collections": ("""
                        SELECT ps.payment_method, SUM(ps.amount_paid) AS total_collection
                        FROM payment_splits ps JOIN customer_sales cs ON cs.sale_id = ps.sale_id
                        WHERE cs.branch_id = %s GROUP BY ps.payment_method
                        ORDER BY total_collection DESC
                    """, (branch_id,)),
                }
                sql, params = scoped[selected]
            else:
                params = ()
            st.dataframe(query_df(sql, params), use_container_width=True, hide_index=True)
        except Error as exc:
            st.error(f"Could not run report: {exc}")


def main():
    if "user" not in st.session_state:
        login_screen()
        return

    user = st.session_state["user"]
    with st.sidebar:
        st.title("📊 Sales Intelligence Hub")
        st.write(f"**Username:** {user['username']}")
        st.write(f"**Role:** {user['role']}")
        if user["branch_id"] is not None:
            st.caption(f"Branch ID: {user['branch_id']}")
        pages = ["Dashboard", "Sales", "Payment Splits", "Search Records", "Calculation Check", "SQL Reports"]
        if user["role"] == "Super Admin":
            pages.append("Branches")
        page = st.radio("Navigation", pages)
        if st.button("Logout", use_container_width=True):
            st.session_state.pop("user", None)
            st.rerun()

    try:
        if page == "Dashboard":
            dashboard_page(user)
        elif page == "Sales":
            sales_page(user)
        elif page == "Payment Splits":
            payments_page(user)
        elif page == "Search Records":
            search_records_page(user)
        elif page == "Calculation Check":
            reconciliation_page(user)
        elif page == "SQL Reports":
            sql_queries_page(user)
        elif page == "Branches" and user["role"] == "Super Admin":
            branches_page()
    except Error as exc:
        st.error(f"Database error: {exc}")
        st.info("Confirm MySQL is running and DB_CONFIG matches your MySQL setup.")


if __name__ == "__main__":
    main()
