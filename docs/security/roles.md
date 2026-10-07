# Roles and permissions

ClueCDC has exactly three roles. They are built in and cannot be customized.
Authorization is enforced by the API; hiding controls in the UI is only a user
experience aid.

| Capability | Viewer | Ops | Admin |
| --- | :---: | :---: | :---: |
| View dashboard, connections, pipelines, schemas, Kafka, Connect, and alerts | Yes | Yes | Yes |
| Create, update, and test connections | No | Yes | Yes |
| Create and update pipelines | No | Yes | Yes |
| Start, stop, restart, and resync pipelines or tables | No | Yes | Yes |
| Acknowledge and silence alerts | No | Yes | Yes |
| Delete pipelines, connections, deliveries, topics, or infrastructure settings | No | No | Yes |
| Configure alert rules/channels and system settings | No | No | Yes |
| View audit logs | No | No | Yes |
| Invite, enable, disable, or change the role of users | No | No | Yes |

`Viewer` is read-only. `Ops` can run day-to-day CDC operations but cannot manage
identity or perform destructive administration. `Admin` has every permission.

Role changes take effect on the user's next API request because each session is
resolved against the current user record. Disabling a user also invalidates all
of that user's sessions.
