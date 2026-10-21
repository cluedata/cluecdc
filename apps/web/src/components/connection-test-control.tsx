"use client";

import { useMutation } from "@tanstack/react-query";
import { Button } from "@cluecdc/ui";
import { Loader2 } from "lucide-react";
import { ErrorPanel, Status } from "./common";
import {
  connectionTestPassed,
  type ConnectionTestFeedback,
} from "@/lib/connection-test";

export function ConnectionTestControl({
  run,
  fingerprint,
  validate,
  disabled = false,
}: {
  run: () => Promise<ConnectionTestFeedback>;
  fingerprint: string;
  validate?: () => Promise<boolean>;
  disabled?: boolean;
}) {
  const test = useMutation({
    mutationFn: async (snapshot: { fingerprint: string; run: typeof run }) => {
      try {
        return {
          fingerprint: snapshot.fingerprint,
          result: await snapshot.run(),
        };
      } catch (error) {
        return { fingerprint: snapshot.fingerprint, error: error as Error };
      }
    },
  });
  const current =
    !test.isPending && test.data?.fingerprint === fingerprint
      ? test.data
      : null;
  return (
    <div className="full-width">
      <div className="connection-test">
        <Button
          type="button"
          variant="outline"
          disabled={disabled || test.isPending}
          onClick={async () => {
            if (validate && !(await validate())) return;
            test.mutate({ fingerprint, run });
          }}
        >
          {test.isPending && (
            <Loader2 size={15} className="spin" aria-hidden="true" />
          )}
          {test.isPending ? "Testing…" : "Test connection"}
        </Button>
      </div>
      {current?.error && <ErrorPanel error={current.error} />}
      {current?.result && (
        <div role="status" aria-live="polite">
          <Status
            value={connectionTestPassed(current.result) ? "HEALTHY" : "ERROR"}
          />{" "}
          <span>
            {connectionTestPassed(current.result)
              ? "Connection test passed"
              : "Connection test failed"}
          </span>
          {current.result.checks?.map((check) => (
            <div key={check.name}>
              <strong>{check.name.replaceAll("_", " ")}</strong>{" "}
              <Status value={check.status} /> {check.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
