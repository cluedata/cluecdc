"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { Copy, UserPlus, Users } from "lucide-react";
import { toast } from "sonner";
import { api, date } from "@/lib/api";
import { ErrorPanel, Field, Loading, PageHeader } from "@/components/common";

type Role = "ADMIN" | "OPS" | "VIEWER";
type User = {
  id: string;
  email: string;
  role: Role;
  status: "INVITED" | "ACTIVE" | "DISABLED";
  last_login_at: string | null;
  created_at: string;
};

export function UsersPage() {
  const cache = useQueryClient();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("OPS");
  const [inviteUrl, setInviteUrl] = useState("");
  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => api<User[]>("/users"),
  });
  const invite = useMutation({
    mutationFn: () =>
      api<{ invite_url: string }>("/users/invite", {
        method: "POST",
        body: JSON.stringify({ email, role }),
      }),
    onSuccess: (result) => {
      setInviteUrl(result.invite_url);
      cache.invalidateQueries({ queryKey: ["users"] });
    },
  });
  const changeRole = useMutation({
    mutationFn: ({ id, nextRole }: { id: string; nextRole: Role }) =>
      api(`/users/${id}/role`, {
        method: "PATCH",
        body: JSON.stringify({ role: nextRole }),
      }),
    onSuccess: () => {
      toast.success("User role updated");
      cache.invalidateQueries({ queryKey: ["users"] });
    },
  });
  const status = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: "disable" | "enable";
    }) => api(`/users/${id}/${action}`, { method: "POST" }),
    onSuccess: (_, variables) => {
      toast.success(
        variables.action === "disable" ? "User disabled" : "User enabled",
      );
      cache.invalidateQueries({ queryKey: ["users"] });
    },
  });

  function closeInvite() {
    setInviteOpen(false);
    setEmail("");
    setRole("OPS");
    setInviteUrl("");
    invite.reset();
  }

  return (
    <>
      <PageHeader
        eyebrow="SETTINGS"
        title="Users"
        description="Invite people and assign one of ClueCDC’s three built-in roles."
      >
        <Button onClick={() => setInviteOpen(true)}>
          <UserPlus size={16} /> Invite User
        </Button>
      </PageHeader>
      {users.isPending ? (
        <Loading />
      ) : users.isError ? (
        <ErrorPanel error={users.error} retry={() => users.refetch()} />
      ) : (
        <section className="panel table-panel">
          <div className="panel-heading">
            <div>
              <h2>Workspace users</h2>
              <p>{users.data.length} accounts</p>
            </div>
          </div>
          <div className="panel-body table-scroll">
            <table className="users-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Last login</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.data.map((user) => (
                  <tr key={user.id}>
                    <td>
                      <strong>{user.email}</strong>
                    </td>
                    <td>
                      <select
                        className="input compact-select"
                        aria-label={`Role for ${user.email}`}
                        value={user.role}
                        disabled={changeRole.isPending}
                        onChange={(event) =>
                          changeRole.mutate({
                            id: user.id,
                            nextRole: event.target.value as Role,
                          })
                        }
                      >
                        <option value="ADMIN">Admin</option>
                        <option value="OPS">Ops</option>
                        <option value="VIEWER">Viewer</option>
                      </select>
                    </td>
                    <td>
                      <span
                        className={`badge user-status-${user.status.toLowerCase()}`}
                      >
                        {user.status.toLowerCase()}
                      </span>
                    </td>
                    <td>{date(user.last_login_at)}</td>
                    <td>{date(user.created_at)}</td>
                    <td>
                      {user.status === "DISABLED" ? (
                        <Button
                          variant="outline"
                          disabled={status.isPending}
                          onClick={() =>
                            status.mutate({ id: user.id, action: "enable" })
                          }
                        >
                          Enable
                        </Button>
                      ) : (
                        <Button
                          variant="ghost"
                          disabled={
                            status.isPending || user.status === "INVITED"
                          }
                          onClick={() =>
                            status.mutate({ id: user.id, action: "disable" })
                          }
                        >
                          Disable
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!users.data.length && (
              <div className="empty">
                <span className="empty-icon">
                  <Users size={22} />
                </span>
                <h3>No users yet</h3>
              </div>
            )}
          </div>
        </section>
      )}
      <Dialog
        open={inviteOpen}
        onOpenChange={(open) => (open ? setInviteOpen(true) : closeInvite())}
        title={inviteUrl ? "Invite created" : "Invite User"}
        description={
          inviteUrl
            ? "Share this link securely. It is displayed only in this dialog."
            : "The user will choose their own password from a single-use link."
        }
      >
        {inviteUrl ? (
          <div className="invite-result">
            <code>{inviteUrl}</code>
            <div className="dialog-actions">
              <Button variant="outline" onClick={closeInvite}>
                Done
              </Button>
              <Button
                onClick={async () => {
                  await navigator.clipboard.writeText(inviteUrl);
                  toast.success("Invite link copied");
                }}
              >
                <Copy size={15} /> Copy invite link
              </Button>
            </div>
          </div>
        ) : (
          <form
            className="form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              invite.mutate();
            }}
          >
            <Field label="Email">
              <Input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="off"
                required
                autoFocus
              />
            </Field>
            <Field label="Role">
              <select
                className="input"
                value={role}
                onChange={(event) => setRole(event.target.value as Role)}
              >
                <option value="ADMIN">Admin</option>
                <option value="OPS">Ops</option>
                <option value="VIEWER">Viewer</option>
              </select>
            </Field>
            {invite.isError && <ErrorPanel error={invite.error} />}
            <div className="dialog-actions">
              <Button type="button" variant="outline" onClick={closeInvite}>
                Cancel
              </Button>
              <Button type="submit" disabled={invite.isPending}>
                {invite.isPending ? "Creating…" : "Create Invite"}
              </Button>
            </div>
          </form>
        )}
      </Dialog>
    </>
  );
}
