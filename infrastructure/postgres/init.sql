\getenv source_password SOURCE_PASSWORD
CREATE ROLE cdc_user WITH LOGIN REPLICATION PASSWORD :'source_password';
GRANT CONNECT, CREATE ON DATABASE commerce TO cdc_user;
GRANT USAGE, CREATE ON SCHEMA public TO cdc_user;
SET ROLE cdc_user;
CREATE TABLE customers (
  id bigserial PRIMARY KEY,
  name text NOT NULL,
  email text NOT NULL UNIQUE,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE orders (
  id bigserial PRIMARY KEY,
  customer_id bigint NOT NULL REFERENCES customers(id),
  total numeric(12,2) NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE payments (
  id bigserial PRIMARY KEY,
  order_id bigint NOT NULL REFERENCES orders(id),
  amount numeric(12,2) NOT NULL,
  status text NOT NULL DEFAULT 'pending'
);
ALTER TABLE customers REPLICA IDENTITY FULL;
ALTER TABLE orders REPLICA IDENTITY FULL;
ALTER TABLE payments REPLICA IDENTITY FULL;
INSERT INTO customers(name, email) VALUES ('Ada Lovelace', 'ada@example.test'), ('Grace Hopper', 'grace@example.test');
INSERT INTO orders(customer_id, total) VALUES (1, 149.00), (2, 299.00);
INSERT INTO payments(order_id, amount) VALUES (1, 149.00);
