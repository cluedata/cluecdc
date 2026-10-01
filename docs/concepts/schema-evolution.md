# Schema evolution

Discovery stores versioned source schemas and computes differences. Delivery validation maps supported source types to the target dialect and rejects ambiguous or unsafe shapes rather than guessing.

After adding a source column, run discovery again and save delivery mappings so ClueCDC refreshes its type metadata. With sink auto-evolution enabled and sufficient target permissions, compatible new columns can be added. Type changes, removed columns, primary-key changes, arrays, enums, unbounded numeric values, and other unsupported shapes require operator review.

Iceberg deliveries can enable schema evolution, but stable identifier fields remain essential for update/delete semantics.
