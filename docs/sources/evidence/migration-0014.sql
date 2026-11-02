BEGIN;

-- Running upgrade 0013 -> 0014

ALTER TABLE connections DROP CONSTRAINT ck_connections_provider;

ALTER TABLE connections ADD CONSTRAINT ck_connections_provider CHECK (provider IN ('POSTGRESQL','MYSQL','AWS_S3','MINIO','AZURE_SYNAPSE','CLICKHOUSE','BIGQUERY','REDSHIFT','MARIADB','SQLSERVER','MONGODB','ORACLE'));

UPDATE alembic_version SET version_num='0014' WHERE alembic_version.version_num = '0013';

COMMIT;
