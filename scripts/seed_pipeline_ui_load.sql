-- Rebuild a deterministic local data set for pipeline UI and list-performance testing.
-- The script only replaces rows whose name/topic prefix carry the UI-load marker.

BEGIN;

DELETE FROM pipeline_destinations
WHERE pipeline_id IN (
  SELECT id FROM pipelines
  WHERE name LIKE 'UI Load Demo %' AND topic_prefix LIKE 'ui_load_%'
);

DELETE FROM pipeline_tables
WHERE pipeline_id IN (
  SELECT id FROM pipelines
  WHERE name LIKE 'UI Load Demo %' AND topic_prefix LIKE 'ui_load_%'
);

DELETE FROM pipelines
WHERE name LIKE 'UI Load Demo %' AND topic_prefix LIKE 'ui_load_%';

DO $$
DECLARE
  pipeline_total CONSTANT integer := 120;
  postgres_source_id uuid;
  mysql_source_id uuid;
  kafka_id uuid;
  connect_id uuid;
  postgres_destination_id uuid;
  mysql_destination_id uuid;
  pipeline_id uuid;
  selected_source_id uuid;
  selected_source_name text;
  selected_source_type text;
  pipeline_name text;
  prefix text;
  pipeline_state text;
  desired_state text;
  table_limit integer;
  category integer;
  i integer;
  created timestamp with time zone;
BEGIN
  SELECT id INTO postgres_source_id
  FROM connections
  WHERE provider = 'POSTGRESQL' AND capabilities_json::jsonb ? 'SOURCE'
  ORDER BY created_at
  LIMIT 1;

  SELECT id INTO mysql_source_id
  FROM connections
  WHERE provider = 'MYSQL' AND capabilities_json::jsonb ? 'SOURCE'
  ORDER BY created_at
  LIMIT 1;

  SELECT id INTO kafka_id FROM kafka_clusters ORDER BY created_at LIMIT 1;
  SELECT id INTO connect_id FROM connect_clusters ORDER BY created_at LIMIT 1;

  SELECT id INTO postgres_destination_id
  FROM connections
  WHERE provider = 'POSTGRESQL' AND capabilities_json::jsonb ? 'DESTINATION'
  ORDER BY created_at
  LIMIT 1;

  SELECT id INTO mysql_destination_id
  FROM connections
  WHERE provider = 'MYSQL' AND capabilities_json::jsonb ? 'DESTINATION'
  ORDER BY created_at
  LIMIT 1;

  IF postgres_source_id IS NULL OR mysql_source_id IS NULL THEN
    RAISE EXCEPTION 'Both PostgreSQL and MySQL sources are required';
  END IF;
  IF kafka_id IS NULL OR connect_id IS NULL THEN
    RAISE EXCEPTION 'A Kafka cluster and a Connect cluster are required';
  END IF;
  IF postgres_destination_id IS NULL OR mysql_destination_id IS NULL THEN
    RAISE EXCEPTION 'Both PostgreSQL and MySQL destinations are required';
  END IF;

  FOR i IN 1..pipeline_total LOOP
    IF i % 2 = 0 THEN
      selected_source_id := postgres_source_id;
      selected_source_name := 'PostgreSQL';
      selected_source_type := 'pg';
    ELSE
      selected_source_id := mysql_source_id;
      selected_source_name := 'MySQL';
      selected_source_type := 'mysql';
    END IF;

    category := i % 6;
    pipeline_state := CASE category
      WHEN 0 THEN 'RUNNING'
      WHEN 1 THEN 'DEGRADED'
      WHEN 2 THEN 'FAILED'
      WHEN 3 THEN 'PAUSED'
      WHEN 4 THEN 'UNKNOWN'
      ELSE 'RUNNING'
    END;
    desired_state := CASE WHEN pipeline_state = 'PAUSED' THEN 'PAUSED' ELSE 'RUNNING' END;
    table_limit := 3 + (i % 18);
    created := now() - ((pipeline_total - i + 1) * interval '5 minutes');
    prefix := 'ui_load_' || selected_source_type || '_' || lpad(i::text, 3, '0');
    pipeline_name := 'UI Load Demo ' || lpad(i::text, 3, '0') || ' - ' ||
      selected_source_name || CASE
        WHEN i % 12 = 0 THEN ' - Customer orders and fulfillment replication'
        WHEN i % 12 = 6 THEN ' - Analytics warehouse synchronization'
        ELSE ''
      END;
    pipeline_id := gen_random_uuid();

    INSERT INTO pipelines (
      id,
      name,
      source_connection_id,
      kafka_cluster_id,
      connect_cluster_id,
      connector_id,
      topic_prefix,
      snapshot_mode,
      config_options,
      desired_state,
      actual_state,
      created_at,
      updated_at
    ) VALUES (
      pipeline_id,
      pipeline_name,
      selected_source_id,
      kafka_id,
      connect_id,
      NULL,
      prefix,
      CASE i % 4 WHEN 0 THEN 'initial' WHEN 1 THEN 'no_data' WHEN 2 THEN 'never' ELSE 'always' END,
      json_build_object(
        'heartbeat_interval_ms', 10000,
        'max_batch_size', 2048,
        'max_queue_size', 8192,
        'poll_interval_ms', 500,
        'additional_debezium_properties', json_build_object(),
        'provider_options', json_build_object()
      ),
      desired_state,
      pipeline_state,
      created,
      created
    );

    INSERT INTO pipeline_tables (
      id,
      pipeline_id,
      schema_name,
      table_name,
      topic_name,
      primary_key_columns,
      destination_schema,
      destination_table,
      initial_data_strategy,
      delete_strategy,
      schema_status,
      destination_status,
      snapshot_status,
      cdc_status,
      snapshot_estimated_rows,
      created_at,
      updated_at
    )
    SELECT
      gen_random_uuid(),
      pipeline_id,
      source_table.schema_name,
      source_table.table_name,
      prefix || '.' || source_table.schema_name || '.' || source_table.table_name,
      source_table.primary_key_columns,
      source_table.schema_name,
      source_table.table_name,
      'BACKFILL',
      'DELETE',
      'IN_SYNC',
      'HEALTHY',
      CASE WHEN i % 4 = 1 THEN 'NOT_REQUIRED' ELSE 'COMPLETED' END,
      CASE WHEN pipeline_state = 'RUNNING' THEN 'STREAMING' ELSE pipeline_state END,
      source_table.estimated_rows,
      created,
      created
    FROM source_tables AS source_table
    WHERE source_table.source_connection_id = selected_source_id
      AND source_table.cdc_ready = true
    ORDER BY md5(source_table.schema_name || '.' || source_table.table_name || i::text)
    LIMIT table_limit;

    -- Pipelines in category 5 intentionally have no destination so the UI also
    -- exercises its "not configured" and degraded states.
    IF category <> 5 THEN
      INSERT INTO pipeline_destinations (
        id,
        pipeline_id,
        destination_connection_id,
        connector_id,
        name,
        delivery_type,
        delivery_mode,
        topic_mapping_json,
        configuration_json,
        desired_state,
        actual_state,
        created_at,
        updated_at
      ) VALUES (
        gen_random_uuid(),
        pipeline_id,
        CASE WHEN i % 2 = 0 THEN mysql_destination_id ELSE postgres_destination_id END,
        NULL,
        'UI Load delivery ' || lpad(i::text, 3, '0') || ' primary',
        'DATABASE',
        CASE WHEN i % 3 = 0 THEN 'append' ELSE 'upsert' END,
        '[]'::json,
        '{}'::json,
        'RUNNING',
        CASE WHEN category = 1 THEN 'DEGRADED' ELSE 'RUNNING' END,
        created,
        created
      );

      IF i % 4 = 0 THEN
        INSERT INTO pipeline_destinations (
          id,
          pipeline_id,
          destination_connection_id,
          connector_id,
          name,
          delivery_type,
          delivery_mode,
          topic_mapping_json,
          configuration_json,
          desired_state,
          actual_state,
          created_at,
          updated_at
        ) VALUES (
          gen_random_uuid(),
          pipeline_id,
          CASE WHEN i % 2 = 0 THEN postgres_destination_id ELSE mysql_destination_id END,
          NULL,
          'UI Load delivery ' || lpad(i::text, 3, '0') || ' secondary',
          'DATABASE',
          'upsert',
          '[]'::json,
          '{}'::json,
          'RUNNING',
          CASE WHEN category = 4 THEN 'UNKNOWN' ELSE 'RUNNING' END,
          created,
          created
        );
      END IF;
    END IF;
  END LOOP;
END $$;

COMMIT;

SELECT
  count(*) AS seeded_pipelines,
  count(*) FILTER (WHERE actual_state = 'RUNNING') AS running,
  count(*) FILTER (WHERE actual_state = 'DEGRADED') AS degraded,
  count(*) FILTER (WHERE actual_state = 'FAILED') AS failed,
  count(*) FILTER (WHERE actual_state = 'PAUSED') AS paused,
  count(*) FILTER (WHERE actual_state = 'UNKNOWN') AS unknown
FROM pipelines
WHERE name LIKE 'UI Load Demo %' AND topic_prefix LIKE 'ui_load_%';
