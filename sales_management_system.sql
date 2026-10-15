-- Sales Intelligence Hub / Sales Management System
-- MySQL 8.0+
-- Run this script in MySQL Workbench.
-- WARNING: This script DROPS and recreates the four existing project tables.
-- You approved replacing the old database data. Keep a backup if needed.

-- WARNING: This removes the ENTIRE old database, including any additional tables.
-- You explicitly approved replacing the old database.
DROP DATABASE IF EXISTS sales_management_system;
CREATE DATABASE sales_management_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE sales_management_system;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS payment_splits;
DROP TABLE IF EXISTS customer_sales;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS branches;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE branches (
    branch_id INT AUTO_INCREMENT PRIMARY KEY,
    branch_name VARCHAR(100) NOT NULL UNIQUE,
    branch_admin_name VARCHAR(100) NULL
) ENGINE=InnoDB;

CREATE TABLE users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    branch_id INT NULL,
    role ENUM('Super Admin', 'Admin') NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE customer_sales (
    sale_id INT AUTO_INCREMENT PRIMARY KEY,
    branch_id INT NOT NULL,
    `date` DATE NOT NULL,
    name VARCHAR(100) NOT NULL,
    mobile_number VARCHAR(15) NOT NULL UNIQUE,
    product_name VARCHAR(30) NOT NULL,
    gross_sales DECIMAL(12,2) NOT NULL,
    received_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    pending_amount DECIMAL(12,2)
        GENERATED ALWAYS AS (gross_sales - received_amount) STORED,
    status ENUM('Open', 'Close') NOT NULL DEFAULT 'Open',
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT chk_gross_sales CHECK (gross_sales > 0),
    CONSTRAINT chk_received_amount CHECK (
        received_amount >= 0 AND received_amount <= gross_sales
    )
) ENGINE=InnoDB;

CREATE TABLE payment_splits (
    payment_id INT AUTO_INCREMENT PRIMARY KEY,
    sale_id INT NOT NULL,
    payment_date DATE NOT NULL,
    amount_paid DECIMAL(12,2) NOT NULL,
    payment_method VARCHAR(50) NOT NULL,
    FOREIGN KEY (sale_id) REFERENCES customer_sales(sale_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT chk_amount_paid CHECK (amount_paid > 0)
) ENGINE=InnoDB;

-- Keep payment totals and Open/Close status synchronized with split payments.
DELIMITER $$

CREATE TRIGGER trg_payment_splits_before_insert
BEFORE INSERT ON payment_splits
FOR EACH ROW
BEGIN
    DECLARE v_gross DECIMAL(12,2);
    DECLARE v_received DECIMAL(12,2);

    SELECT gross_sales, received_amount
      INTO v_gross, v_received
      FROM customer_sales
     WHERE sale_id = NEW.sale_id
     FOR UPDATE;

    IF NEW.amount_paid <= 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Payment amount must be greater than zero';
    END IF;

    IF v_received + NEW.amount_paid > v_gross THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Payment exceeds the pending balance';
    END IF;
END$$

DELIMITER ;

-- Use the single-statement AFTER INSERT trigger that succeeded in MySQL Workbench.
CREATE TRIGGER trg_payment_splits_after_insert
AFTER INSERT ON payment_splits
FOR EACH ROW
UPDATE customer_sales
SET status = IF(
    received_amount + NEW.amount_paid >= gross_sales,
    'Close',
    'Open'
),
received_amount = received_amount + NEW.amount_paid
WHERE sale_id = NEW.sale_id;



-- The real data is imported from your four CSV files using import_csv_data.py.
-- No invented sample records are inserted.

-- Required project analytical queries (20 examples)
-- 1. All sales
SELECT * FROM customer_sales;

-- 2. All branches
SELECT * FROM branches;

-- 3. All payment split records
SELECT * FROM payment_splits;

-- 4. Open sales
SELECT * FROM customer_sales WHERE status = 'Open';

-- 5. Sales from Chennai
SELECT cs.*
FROM customer_sales cs
JOIN branches b ON b.branch_id = cs.branch_id
WHERE b.branch_name = 'Chennai';

-- 6. Total gross sales
SELECT COALESCE(SUM(gross_sales), 0) AS total_gross_sales FROM customer_sales;

-- 7. Total received
SELECT COALESCE(SUM(received_amount), 0) AS total_received FROM customer_sales;

-- 8. Total pending
SELECT COALESCE(SUM(pending_amount), 0) AS total_pending FROM customer_sales;

-- 9. Count sales per branch
SELECT b.branch_name, COUNT(cs.sale_id) AS sales_count
FROM branches b LEFT JOIN customer_sales cs ON cs.branch_id = b.branch_id
GROUP BY b.branch_id, b.branch_name;

-- 10. Average gross sales
SELECT AVG(gross_sales) AS average_gross_sales FROM customer_sales;

-- 11. Sales with branch name
SELECT cs.*, b.branch_name
FROM customer_sales cs JOIN branches b ON b.branch_id = cs.branch_id;

-- 12. Sales with total payments
SELECT cs.sale_id, cs.name, cs.gross_sales,
       COALESCE(SUM(ps.amount_paid), 0) AS total_payments
FROM customer_sales cs
LEFT JOIN payment_splits ps ON ps.sale_id = cs.sale_id
GROUP BY cs.sale_id, cs.name, cs.gross_sales;

-- 13. Branch-wise gross sales
SELECT b.branch_name, COALESCE(SUM(cs.gross_sales), 0) AS gross_sales
FROM branches b LEFT JOIN customer_sales cs ON cs.branch_id = b.branch_id
GROUP BY b.branch_id, b.branch_name;

-- 14. Sales with payment methods
SELECT cs.sale_id, cs.name, ps.payment_method, ps.amount_paid
FROM customer_sales cs
LEFT JOIN payment_splits ps ON ps.sale_id = cs.sale_id;

-- 15. Sales with branch admin
SELECT cs.sale_id, cs.name, b.branch_name, b.branch_admin_name
FROM customer_sales cs JOIN branches b ON b.branch_id = cs.branch_id;

-- 16. Pending amount above 5000
SELECT * FROM customer_sales WHERE pending_amount > 5000;

-- 17. Top 3 gross sales
SELECT * FROM customer_sales ORDER BY gross_sales DESC LIMIT 3;

-- 18. Branch with highest gross sales
SELECT b.branch_name, SUM(cs.gross_sales) AS gross_sales
FROM branches b JOIN customer_sales cs ON cs.branch_id = b.branch_id
GROUP BY b.branch_id, b.branch_name
ORDER BY gross_sales DESC LIMIT 1;

-- 19. Monthly sales summary
SELECT YEAR(`date`) AS sales_year, MONTH(`date`) AS sales_month,
       SUM(gross_sales) AS gross_sales
FROM customer_sales
GROUP BY YEAR(`date`), MONTH(`date`)
ORDER BY sales_year, sales_month;

-- 20. Payment method-wise collection
SELECT payment_method, SUM(amount_paid) AS total_collection
FROM payment_splits
GROUP BY payment_method
ORDER BY total_collection DESC;
