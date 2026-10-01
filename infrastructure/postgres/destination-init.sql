\getenv destination_password DESTINATION_PASSWORD
CREATE ROLE delivery_user WITH LOGIN PASSWORD :'destination_password';
GRANT CONNECT ON DATABASE analytics TO delivery_user;
GRANT USAGE, CREATE ON SCHEMA public TO delivery_user;
CREATE SCHEMA analytics AUTHORIZATION delivery_user;
SET ROLE delivery_user;
CREATE TABLE public.customers (
  id bigint PRIMARY KEY,
  name text NOT NULL,
  email text NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE TABLE public.orders (
  id bigint PRIMARY KEY,
  customer_id bigint NOT NULL,
  total numeric(12,2) NOT NULL,
  status text NOT NULL,
  updated_at timestamptz NOT NULL
);
