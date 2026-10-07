import { PageHeader } from "@/components/common";

export default function Page() {
  return (
    <>
      <PageHeader
        eyebrow="SETTINGS"
        title="Security"
        description="ClueCDC local authentication and session policy."
      />
      <section className="panel security-summary">
        <h2>Password and session security</h2>
        <dl className="facts">
          <dt>Sign-in method</dt>
          <dd>Email and password</dd>
          <dt>Password storage</dt>
          <dd>Argon2id hash</dd>
          <dt>Browser session</dt>
          <dd>HttpOnly, SameSite cookie</dd>
          <dt>Minimum password length</dt>
          <dd>12 characters</dd>
          <dt>User onboarding</dt>
          <dd>Single-use, expiring invite links</dd>
        </dl>
      </section>
    </>
  );
}
