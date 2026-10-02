import { useEffect, useRef, useState } from "react";
import { initials, useAuth } from "../auth";
import { IconLock, IconLogout, IconUsers } from "../Icons";
import ChangePasswordDialog from "./ChangePasswordDialog";
import UserManagement from "./UserManagement";

interface Props {
  /** Which way the menu opens: down from a top bar, or along a sidebar. */
  align?: "left" | "right";
}

/** Who is signed in, and the things only they can do about it. */
function UserMenu({ align = "right" }: Props) {
  const { user, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<"password" | "users" | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    }
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  if (!user) return null;

  function pick(next: "password" | "users") {
    setOpen(false);
    setDialog(next);
  }

  return (
    <div className="user-menu" ref={root}>
      <button
        className="user-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account: ${user.display_name}`}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="avatar" aria-hidden="true">
          {initials(user)}
        </span>
        <span className="user-menu__label">
          <span className="user-menu__name">{user.display_name}</span>
          <span className={`user-menu__role user-menu__role--${user.role}`}>
            {user.role === "admin" ? "Admin" : "User"}
          </span>
        </span>
        <span className="user-menu__caret" aria-hidden="true">
          ▾
        </span>
      </button>

      {open && (
        <div className={`user-menu__panel user-menu__panel--${align}`} role="menu">
          <div className="user-menu__who">
            <span className="avatar avatar--lg" aria-hidden="true">
              {initials(user)}
            </span>
            <span>
              <strong>{user.display_name}</strong>
              <span className="user-menu__meta">
                @{user.username} · {user.role === "admin" ? "Admin" : "User"}
              </span>
            </span>
          </div>
          {user.role === "admin" && (
            <button role="menuitem" onClick={() => pick("users")}>
              <IconUsers /> Manage users
            </button>
          )}
          <button role="menuitem" onClick={() => pick("password")}>
            <IconLock /> Change password
          </button>
          <button role="menuitem" className="user-menu__signout" onClick={() => signOut()}>
            <IconLogout /> Sign out
          </button>
        </div>
      )}

      {dialog === "password" && <ChangePasswordDialog onClose={() => setDialog(null)} />}
      {dialog === "users" && <UserManagement onClose={() => setDialog(null)} />}
    </div>
  );
}

export default UserMenu;
