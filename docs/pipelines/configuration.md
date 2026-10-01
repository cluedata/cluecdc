# Pipeline configuration

Core settings include source, Kafka/Connect clusters, unique topic prefix, snapshot mode, selected tables, and provider-specific options. Connector configuration is generated server-side; browser input cannot inject arbitrary secret values.

Preview before deployment and treat identity settings as immutable. For a materially different topic namespace, slot/publication, or MySQL server identity, create a reviewed replacement pipeline and migration plan.
