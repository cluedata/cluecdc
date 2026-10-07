"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { Button, Input } from "@cluecdc/ui";
import { CheckCircle2, GitBranch, ShieldCheck } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Field } from "@/components/common";

function AuthFrame({ children }: { children: React.ReactNode }) {
  return (
    <main className="auth-shell">
      <section className="auth-brand-panel" aria-label="ClueCDC">
        <Link href="/" className="auth-brand">
          <span className="brand-mark">
            <GitBranch size={30} />
          </span>
          <strong>
            Clue<span>CDC</span>
          </strong>
        </Link>
        <div>
          <p className="eyebrow">DATA MOVEMENT CONTROL PLANE</p>
          <h1>Operate change data capture with confidence.</h1>
          <p>
            One focused workspace for pipelines, Kafka, delivery health, and
            operational response.
          </p>
        </div>
        <small>Self-hosted · Open source · Your infrastructure</small>
      </section>
      <section className="auth-form-panel">{children}</section>
    </main>
  );
}

function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p className="auth-error" role="alert">
      {error instanceof Error ? error.message : "The operation failed."}
    </p>
  );
}

export function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(undefined);
    const form = new FormData(event.currentTarget);
    try {
      await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: form.get("email"),
          password: form.get("password"),
        }),
      });
      router.replace("/overview");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.code === "INVALID_CREDENTIALS"
          ? new Error("Invalid email or password.")
          : caught,
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthFrame>
      <div className="auth-card">
        <div className="auth-heading">
          <span className="auth-icon">
            <ShieldCheck size={20} />
          </span>
          <p className="eyebrow">SECURE ACCESS</p>
          <h1>Sign in to ClueCDC</h1>
          <p>Use the account created by your ClueCDC administrator.</p>
        </div>
        <form onSubmit={submit} className="auth-form">
          <Field label="Email">
            <Input
              name="email"
              type="email"
              autoComplete="email"
              required
              autoFocus
            />
          </Field>
          <Field label="Password">
            <Input
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </Field>
          <ErrorMessage error={error} />
          <Button type="submit" disabled={pending}>
            {pending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </div>
    </AuthFrame>
  );
}

type InviteDetails = { email: string; role: string; valid: true };

export function InvitePage({ token }: { token: string }) {
  const [invite, setInvite] = useState<InviteDetails>();
  const [loadError, setLoadError] = useState(false);
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const [complete, setComplete] = useState(false);

  useEffect(() => {
    api<InviteDetails>(`/invites/${encodeURIComponent(token)}`)
      .then(setInvite)
      .catch(() => setLoadError(true));
  }, [token]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") || "");
    const confirmation = String(form.get("confirmation") || "");
    if (password.length < 12) {
      setError(new Error("Password must be at least 12 characters."));
      return;
    }
    if (password !== confirmation) {
      setError(new Error("Passwords do not match."));
      return;
    }
    setPending(true);
    setError(undefined);
    try {
      await api(`/invites/${encodeURIComponent(token)}/accept`, {
        method: "POST",
        body: JSON.stringify({ password, confirm_password: confirmation }),
      });
      setComplete(true);
    } catch (caught) {
      setError(caught);
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthFrame>
      <div className="auth-card">
        {loadError ? (
          <div className="auth-heading">
            <p className="eyebrow">INVITATION</p>
            <h1>This invite is invalid or has expired.</h1>
            <p>Ask your ClueCDC administrator to create a new invite.</p>
          </div>
        ) : complete ? (
          <div className="auth-heading">
            <span className="auth-icon success">
              <CheckCircle2 size={20} />
            </span>
            <p className="eyebrow">ACCOUNT ACTIVATED</p>
            <h1>Your account is ready.</h1>
            <p>You can now sign in with your email and new password.</p>
            <Button asChild>
              <Link href="/login">Sign in</Link>
            </Button>
          </div>
        ) : !invite ? (
          <p className="muted">Checking invitation…</p>
        ) : (
          <>
            <div className="auth-heading">
              <p className="eyebrow">WELCOME TO CLUECDC</p>
              <h1>Create your password</h1>
              <p>
                <strong>{invite.email}</strong> · {invite.role.toLowerCase()}
              </p>
            </div>
            <form onSubmit={submit} className="auth-form">
              <Field label="Password" hint="Use at least 12 characters.">
                <Input
                  name="password"
                  type="password"
                  minLength={12}
                  autoComplete="new-password"
                  required
                  autoFocus
                />
              </Field>
              <Field label="Confirm password">
                <Input
                  name="confirmation"
                  type="password"
                  minLength={12}
                  autoComplete="new-password"
                  required
                />
              </Field>
              <ErrorMessage error={error} />
              <Button type="submit" disabled={pending}>
                {pending ? "Activating…" : "Activate account"}
              </Button>
            </form>
          </>
        )}
      </div>
    </AuthFrame>
  );
}
