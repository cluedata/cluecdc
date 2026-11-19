import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parseEnv } from "node:util";

// Match Compose: shell, then .env, then the shared local password.
const root = resolve(process.cwd(), "../..");
const envPath = process.env.CLUECDC_ENV_FILE || resolve(root, ".env");
const values = {
  ...parseEnv(readFileSync(resolve(root, ".env.example"), "utf8")),
  ...(existsSync(envPath) ? parseEnv(readFileSync(envPath, "utf8")) : {}),
  ...process.env,
};

export function fixtureEnv(key: string): string {
  return (
    values[key] ||
    (key.endsWith("PASSWORD") || key === "MINIO_SECRET_KEY"
      ? values.CLUECDC_LOCAL_PASSWORD
      : "") ||
    ""
  );
}
