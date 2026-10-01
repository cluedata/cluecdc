# Signal Console

ClueCDC uses a warm, compact control-plane UI. Design tokens live in
`apps/web/src/app/tokens.css`; shared component rules live in
`apps/web/src/app/design-system.css`. Existing API calls, query keys, routes,
resource actions and form validation remain in place.

## Typography and surfaces

Space Grotesk is used for headings, navigation and primary labels, Manrope for
body text, and JetBrains Mono for measurements and technical values. Fonts are
self-hosted through `next/font/local` in the root layout. Their upstream OFL
licenses are included beside the font files.

The canvas is `#F4F2EC`; panels use `#FAF9F5`, one-pixel dividers and three-pixel
corners. Buttons use four-pixel corners and an ink primary action. Metrics use
divider-based strips instead of individual cards. Table rows use a 42px minimum
height and scroll within the table at narrow widths.

## Shared components

- `Shell`: 184px navigation, continuous signal rail, compact command bar and
  mobile drawer. Connect clusters and connectors remain reachable after Audit;
  Settings and API Reference stay at the bottom.
- `InventoryStrip`: resource counts derived from each page's existing query.
  Incident counts describe the currently matching results.
- `StatusBadge`: status text and a small colored dot; color is never the only
  indication of state.
- `DataTable`: existing sorting, filtering, pagination, column visibility,
  resource links and keyboard row navigation with compact console styling.
- `DataFlowRail`: reusable topology used on Overview, pipeline details and
  destination details. Each stage retains its resource link and runtime state.

## Flow semantics

The source health, capture runtime, Kafka health and individual delivery runtime
are independent. Overview joins destinations to pipelines using the delivery's
`pipeline_id`, and renders a separate branch for each delivery. Pipeline details
use their existing detailed delivery inventory.

Healthy and running states use the signal color; deploying and snapshotting are
active; degraded/warning/pending states use amber; failures use red; paused,
unconfigured and unknown states use a quiet gray rail and explicit status text.
Flow segments stop animating beside paused or failing stages. Reduced-motion
preferences disable all flow animation. Small screens use a vertical topology.

Missing throughput and lag remain unavailable. Error rate and event freshness
are also unavailable because the existing Overview API does not measure them.
Open incident counts are not converted into an event error rate. The destination
detail rail labels Kafka health unknown because its data does not observe broker
health. Historical metrics remain accessible on Monitoring.

## Verification

```powershell
npm.cmd run lint
npm.cmd run typecheck
npm.cmd test
npm.cmd run build
npm.cmd run dev -w @cluecdc/web -- --port 3001
node apps/web/scripts/inspect-pages.mjs http://localhost:3001
node apps/web/scripts/inspect-signal.mjs http://localhost:3001
```

The page inspection checks 16 routes at 1280, 1440, 1600, 1920, 1024 and 390px.
The Signal Console inspection checks Overview and real source, pipeline and
destination details at those desktop widths plus 1024, 768 and 390px. It also
checks reduced motion, refresh controls, search, table controls, keyboard tabs,
drawers and mobile navigation, using available backend resources without
creating or modifying them. Screenshots are written to `artifacts/signal-console`.

Regression tests cover independent capture/delivery failures, multiple downstream
deliveries and unconfigured destinations with unavailable metrics.
