import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, getAuthToken, onUnauthorized, setAuthToken } from "./api";
import type { SignedIn, User } from "./types";

export interface AuthState {
  /** Null until signed in. */
  user: User | null;
  /** True while a remembered sign-in is being checked on launch. */
  checking: boolean;
  /** Set when the app was sent back to sign in by the server, so the
   *  sign-in screen can say why rather than appearing out of nowhere. */
  expiredMessage: string | null;
  signedIn: (result: SignedIn) => void;
  signOut: () => Promise<void>;
  /** After the user changes their own details. */
  replaceUser: (user: User) => void;
}

const AuthContext = createContext<AuthState | null>(null);

/**
 * Who is signed in, kept for the whole app.
 *
 * The token lives in api.ts so every request carries it; this holds
 * the user it belongs to and reacts when the server stops accepting it.
 */
export function useAuthSession(): AuthState {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(() => getAuthToken() !== null);
  const [expiredMessage, setExpiredMessage] = useState<string | null>(null);

  useEffect(() => {
    onUnauthorized(() => {
      setAuthToken(null);
      setUser((current) => {
        if (current) setExpiredMessage("Your sign-in ended. Please sign in again.");
        return null;
      });
    });
    return () => onUnauthorized(null);
  }, []);

  // A remembered sign-in is checked, not trusted: it may have expired
  // or been switched off since the app was last open.
  useEffect(() => {
    if (getAuthToken() === null) return;
    let cancelled = false;
    api
      .getMe()
      .then((me) => !cancelled && setUser(me))
      .catch(() => {
        if (cancelled) return;
        setAuthToken(null);
      })
      .finally(() => !cancelled && setChecking(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const signedIn = useCallback((result: SignedIn) => {
    setAuthToken(result.token);
    setExpiredMessage(null);
    setUser(result.user);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Signed out here whatever the server says.
    }
    setAuthToken(null);
    setUser(null);
  }, []);

  return { user, checking, expiredMessage, signedIn, signOut, replaceUser: setUser };
}

export const AuthProvider = AuthContext.Provider;

export function useAuth(): AuthState {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth outside AuthProvider");
  return auth;
}

/** "Harsh Sharma" -> "HS", for the avatar. */
export function initials(user: Pick<User, "display_name" | "username">): string {
  const name = user.display_name.trim() || user.username;
  const parts = name.split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : name.slice(0, 2);
  return letters.toUpperCase();
}
