# Pull request process

1. Branch from `main` and keep the change focused.
2. Add tests, migrations, and docs together with behavior changes.
3. Run repository, frontend, backend, build, Compose, and docs checks that apply.
4. Complete the pull-request template, including operational impact and verification evidence.
5. Resolve CI and review feedback without rewriting unrelated code.

Maintainers merge only after required checks and review. Releases follow semantic versioning: `0.x` minor versions may still include documented breaking changes; patch releases should remain compatible.
