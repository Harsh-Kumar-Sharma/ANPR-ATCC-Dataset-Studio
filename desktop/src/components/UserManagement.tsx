import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { initials, useAuth } from "../auth";
import { IconAlert, IconCheck, IconPlus } from "../Icons";
import type { User, UserRole } from "../types";
import Modal from "./Modal";

interface Props {
  onClose: () => void;
}

function describe(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

function when(iso: string | null): string {
  if (!iso) return "Never";
  // Naive UTC from the server; read it as UTC, not local time.
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** What the row is asking the admin to confirm, if anything. */
type Pending = { kind: "reset"; user: User } | { kind: "delete"; user: User } | null;

/**
 * Who can sign in, and what they may do. Admins only.
 *
 * Switching someone off is the everyday way to stop them signing in:
 * their name stays on the list and can be switched back on. Deleting is
 * for an account made by mistake.
 */
function UserManagement({ onClose }: Props) {
  const { user: me, replaceUser } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [resetTo, setResetTo] = useState("");

  const [adding, setAdding] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newName, setNewName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<UserRole>("user");

  const load = useCallback(async () => {
    try {
      setUsers(await api.listUsers());
      setError(null);
    } catch (e) {
      setError(describe(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function act(userId: string, what: () => Promise<unknown>, said: string) {
    setBusyId(userId);
    setError(null);
    setNotice(null);
    try {
      await what();
      setNotice(said);
      await load();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusyId(null);
    }
  }

  async function change(user: User, changes: { role?: UserRole; is_active?: boolean }, said: string) {
    await act(
      user.id,
      async () => {
        const updated = await api.updateUser(user.id, changes);
        if (updated.id === me?.id) replaceUser(updated);
      },
      said,
    );
  }

  async function addUser(e: React.FormEvent) {
    e.preventDefault();
    await act(
      "new",
      async () => {
        await api.createUser({
          username: newUsername.trim(),
          password: newPassword,
          display_name: newName.trim(),
          role: newRole,
        });
        setNewUsername("");
        setNewName("");
        setNewPassword("");
        setNewRole("user");
        setAdding(false);
      },
      `Added ${newUsername.trim().toLowerCase()}. They can sign in now.`,
    );
  }

  async function confirmPending() {
    if (!pending) return;
    const { kind, user } = pending;
    if (kind === "reset") {
      await act(
        user.id,
        () => api.resetUserPassword(user.id, resetTo),
        `New password set for ${user.username}. They have been signed out everywhere.`,
      );
    } else {
      await act(user.id, () => api.deleteUser(user.id), `Deleted ${user.username}.`);
    }
    setPending(null);
    setResetTo("");
  }

  const admins = users.filter((u) => u.role === "admin" && u.is_active).length;

  return (
    <Modal title="Users" onClose={onClose} wide>
      <div className="users-toolbar">
        <p className="users-summary">
          {users.length} user{users.length === 1 ? "" : "s"} · {admins} admin{admins === 1 ? "" : "s"}
        </p>
        {!adding && (
          <button className="btn-primary" onClick={() => setAdding(true)}>
            <IconPlus /> Add user
          </button>
        )}
      </div>

      {error && (
        <p className="error" role="alert">
          <IconAlert /> {error}
        </p>
      )}
      {notice && (
        <p className="status" role="status">
          <IconCheck /> {notice}
        </p>
      )}

      {adding && (
        <form className="users-add" onSubmit={addUser}>
          <label className="auth-field">
            Username
            <input type="text" autoFocus value={newUsername} onChange={(e) => setNewUsername(e.target.value)} />
          </label>
          <label className="auth-field">
            Name
            <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} />
          </label>
          <label className="auth-field">
            Password
            <input
              type="password"
              autoComplete="new-password"
              value={newPassword}
              placeholder="At least 8 characters"
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </label>
          <label className="auth-field">
            Role
            <select value={newRole} onChange={(e) => setNewRole(e.target.value as UserRole)}>
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <div className="users-add__actions">
            <button type="button" onClick={() => setAdding(false)}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={busyId === "new" || !newUsername.trim() || !newPassword}
            >
              {busyId === "new" ? "Adding…" : "Add user"}
            </button>
          </div>
        </form>
      )}

      {pending && (
        <div className={pending.kind === "delete" ? "delete-confirm" : "users-confirm"} role="alert">
          {pending.kind === "reset" ? (
            <>
              <p className="delete-confirm-title">
                New password for <strong>{pending.user.username}</strong>
              </p>
              <label className="auth-field">
                New password
                <input
                  type="password"
                  autoComplete="new-password"
                  autoFocus
                  value={resetTo}
                  placeholder="At least 8 characters"
                  onChange={(e) => setResetTo(e.target.value)}
                />
              </label>
              <p className="auth-hint">They will be signed out everywhere and need this password next time.</p>
            </>
          ) : (
            <p className="delete-confirm-title">
              Delete <strong>{pending.user.username}</strong>? Their work in projects stays. To stop them signing in
              but keep the account, switch it off instead.
            </p>
          )}
          <div className="delete-confirm-actions">
            <button onClick={() => setPending(null)}>Cancel</button>
            <button
              className={pending.kind === "delete" ? "btn-danger" : "btn-primary"}
              disabled={busyId !== null || (pending.kind === "reset" && !resetTo)}
              onClick={confirmPending}
            >
              {pending.kind === "delete" ? "Delete user" : "Set password"}
            </button>
          </div>
        </div>
      )}

      <div className="users-table-wrap">
        <table className="users-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              <th>Status</th>
              <th>Last sign-in</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const isMe = u.id === me?.id;
              const busy = busyId === u.id;
              return (
                <tr key={u.id} className={u.is_active ? "" : "users-table__off"}>
                  <td>
                    <span className="users-table__who">
                      <span className="avatar avatar--sm" aria-hidden="true">
                        {initials(u)}
                      </span>
                      <span>
                        <strong>{u.display_name}</strong>
                        {isMe && <span className="badge badge-accent users-table__you">You</span>}
                        <span className="users-table__username">@{u.username}</span>
                      </span>
                    </span>
                  </td>
                  <td>
                    <select
                      aria-label={`Role of ${u.username}`}
                      value={u.role}
                      disabled={busy}
                      onChange={(e) =>
                        change(u, { role: e.target.value as UserRole }, `${u.username} is now ${e.target.value === "admin" ? "an admin" : "a user"}.`)
                      }
                    >
                      <option value="user">User</option>
                      <option value="admin">Admin</option>
                    </select>
                  </td>
                  <td>
                    <span className={`badge ${u.is_active ? "badge-success" : "badge-neutral"}`}>
                      {u.is_active ? "Active" : "Off"}
                    </span>
                  </td>
                  <td className="users-table__when">{when(u.last_login_at)}</td>
                  <td>
                    <span className="users-table__actions">
                      {!isMe && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            change(
                              u,
                              { is_active: !u.is_active },
                              u.is_active ? `${u.username} is switched off and signed out.` : `${u.username} can sign in again.`,
                            )
                          }
                        >
                          {u.is_active ? "Switch off" : "Switch on"}
                        </button>
                      )}
                      <button
                        disabled={busy}
                        onClick={() => {
                          setResetTo("");
                          setPending({ kind: "reset", user: u });
                        }}
                      >
                        Reset password
                      </button>
                      {!isMe && (
                        <button
                          className="btn-danger-ghost"
                          aria-label={`Delete ${u.username}`}
                          disabled={busy}
                          onClick={() => setPending({ kind: "delete", user: u })}
                        >
                          Delete
                        </button>
                      )}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}

export default UserManagement;
