INSERT INTO customers(name, email)
VALUES ('CDC demo', 'demo-' || gen_random_uuid() || '@example.test');
INSERT INTO orders(customer_id, total)
SELECT max(id), 42.50 FROM customers;
UPDATE customers SET name = name || ' updated', updated_at = now()
WHERE id = (SELECT max(id) FROM customers);
UPDATE orders SET status = 'paid', updated_at = now()
WHERE id = (SELECT max(id) FROM orders);
DELETE FROM orders WHERE id = (SELECT max(id) FROM orders);
DELETE FROM customers WHERE id = (SELECT max(id) FROM customers);
