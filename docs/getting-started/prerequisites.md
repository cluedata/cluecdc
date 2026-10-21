# Prerequisites

## Docker Compose path

- Git
- Docker Engine or Docker Desktop with Linux containers and BuildKit enabled
- Docker Compose v2.24+ or v5 (`docker compose`, not legacy `docker-compose` v1)
- At least 6 GB free Docker memory; 8 GB or more recommended for builds/test fixtures
- At least 20 GB free Docker disk space for images, build cache, and local data
- Core ports 3000, 8000, 8083, and 9092 available, or overridden in `.env`
- Test fixtures additionally use ports 5434-5435, 3307-3308, and 9000

Host Python and Node.js are not required for the Compose quick start. Copying
`.env.example` supplies public demo credentials for a loopback-only installation.
See [configuration](../operations/configuration.md) for optional unique local
secrets and production requirements.

### Host platforms

| Host | Container engine | CPU architecture |
| --- | --- | --- |
| Ubuntu / other Linux distributions supported by Docker | Docker Engine with Buildx and the Compose plugin, or Docker Desktop | x86-64 or ARM64 |
| macOS Intel | Docker Desktop for Intel | x86-64 |
| macOS Apple Silicon | Docker Desktop for Apple Silicon | ARM64 |
| Windows | Docker Desktop using WSL 2 and Linux containers | x86-64; ARM64 requires a compatible Docker Desktop/WSL installation |

All services are **Linux containers**, including when launched from PowerShell
or macOS. Native Windows containers, 32-bit CPUs, and other architectures are
outside this Compose configuration's support boundary. Docker automatically
selects the matching image; do not force `linux/amd64` on Apple Silicon.
See [Docker's Windows installation requirements](https://docs.docker.com/desktop/setup/install/windows-install/)
and [multi-platform image behavior](https://docs.docker.com/build/building/multi-platform/).

The application images are built locally for the engine's CPU. Their base
images, PostgreSQL, MySQL, Kafka, and Debezium Connect provide both `linux/amd64`
and `linux/arm64` variants. The first build needs internet access to Docker Hub,
Quay, GitHub, npm, PyPI, Debian package repositories, and (for the optional MinIO
fixture) the Go module proxy. A cold fixture build can take substantially longer
than starting already-built images.

Before building, confirm that the server reports `linux` and the expected CPU:

```bash
docker info --format '{{.OSType}}/{{.Architecture}}'
docker compose version
docker buildx version
```

On Docker Desktop, start the engine first. With Docker Engine on Linux, ensure
your user can access the daemon. The core stack uses named volumes rather than
host filesystem mounts; test fixtures bind-mount a few repository files read-only.
Allow the checkout directory in Docker Desktop's file-sharing settings if needed.
On Windows, a checkout inside the WSL filesystem generally has faster build I/O.

## Host development path

- Node.js 24 and npm
- Python 3.12
- Docker Compose for metadata and data-plane dependencies

Windows PowerShell users can replace `cp` with `Copy-Item` and `python` with
`py -3` when using the Windows launcher; on macOS/Linux the Python command may
be `python3`. Commands shown inside containers use the image's Linux shell.
For multiline host commands, Bash uses `\` and PowerShell uses a backtick;
the one-line Compose commands work in both. Keep the Git-enforced LF endings
on `*.sh` files to prevent MySQL fixture initialization failures on Windows.

## Source database access

An external PostgreSQL source needs logical WAL, replication capacity, a stable primary key, `SELECT`, replication privileges, and ownership compatible with filtered publication creation. MySQL needs row-based binlogs with full row images and the documented replication grants. ClueCDC reports readiness problems; it does not silently alter production servers.
