# Roles and permissions

ClueCDC has exactly three roles. They are built in and cannot be customized.
Authorization is enforced by the API; hiding controls in the UI is only a user
experience aid.

| Capability | Viewer | Ops | Admin |
| --- | :---: | :---: | :---: |
| View Overview, Pipelines, and Deliveries | Yes | Yes | Yes |
| View Sources and Destinations | No | Yes | Yes |
| Create, update, operate, and delete Pipelines | No | Yes | Yes |
| Create, update, operate, and delete Deliveries | No | Yes | Yes |
| Create, update, test, or delete Sources and Destinations | No | No | Yes |
| Open Kafka/Connect infrastructure pages or mutate infrastructure | No | No | Yes |
| View or manage alerts, errors, audit, and system settings | No | No | Yes |
| Invite, enable, disable, change the role of, or delete users | No | No | Yes |

`Viewer` has a deliberately narrow read-only workspace containing Overview,
Pipelines, and Deliveries. `Ops` adds read-only Source and Destination access and
full day-to-day lifecycle control over Pipelines and Deliveries. `Admin` has
every permission. Navigation, actions, and detail tabs outside a role's access
are hidden, while the API independently enforces the same boundaries.

Ops has read-only API access to existing Kafka and Connect inventory so the
pipeline wizard can select runtime clusters. Standalone infrastructure pages
and every infrastructure mutation remain hidden and Admin-only.

Role changes and account disabling invalidate all of the affected user's
sessions. The sole active Admin cannot be demoted or deleted. Every non-public
API route has a server-side principal/permission dependency; route-regression
tests fail when a new endpoint omits that boundary.

ClueCDC is a single-workspace control plane, so there is no tenant membership
layer. Nested resource routes verify their parent relationship and return 404
for mismatched identifiers. Generic webhook destinations reject loopback,
private, link-local, metadata, and rebinding targets; source/database and MinIO
connections intentionally permit private addresses because private data-plane
access is their purpose and remain Admin-managed.
