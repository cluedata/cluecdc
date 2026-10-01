# Development architecture

ClueCDC is a modular monolith with a Next.js App Router frontend and a FastAPI/SQLAlchemy backend. HTTP contracts and authorization live in `apps/api/app/api` and `schemas`; orchestration in `services`; external calls in `adapters`; database capability plugins in `providers`; persistence entities in `models` and Alembic migrations.

The frontend uses TypeScript, React, TanStack Query/Table, React Hook Form, Zod, Radix-based shared components, and a same-origin API proxy. Shared TypeScript contracts live in `packages/contracts`; reusable controls live in `packages/ui`.

Keep row events in the data plane. New control-plane features should store configuration and operational metadata, not become a bespoke CDC transport.
