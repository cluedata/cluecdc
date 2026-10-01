# PostgreSQL to Iceberg demo

Start the Lakehouse profile and load `customers.sql`. In ClueCDC, discover the
PostgreSQL source and create a pipeline for `public.customers`. Create a MinIO
connection, use it for an Iceberg destination, and deploy its delivery.

Inspect the delivery task and MinIO objects, then exercise update, compatible
schema evolution, and delete propagation at the source:

```sql
UPDATE public.customers SET name = 'Jane' WHERE id = 1;
ALTER TABLE public.customers ADD COLUMN phone VARCHAR(50);
UPDATE public.customers SET phone = '+1-555-0100' WHERE id = 1;
DELETE FROM public.customers WHERE id = 1;
```

Wait for each commit interval and confirm new Iceberg metadata snapshots are
created while the delivery task remains healthy.
