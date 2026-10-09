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

Role changes take effect on the user's next API request because each session is
resolved against the current user record. Disabling a user also invalidates all
of that user's sessions.
