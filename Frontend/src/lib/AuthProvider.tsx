import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import * as auth from "./auth";
import type { Session } from "./auth";
import { MOCK } from "./mode";
import { isAdmin } from "./roles";

export interface AuthContextValue {
  session: Session | null;
  configured: boolean;
  signIn: (returnTo?: string | null) => Promise<void>;
  finishLogin: (search: string) => Promise<string>;
  mockSignIn: (username: string, groups: string[]) => void;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// Keeps the Cognito session (see auth.ts) in React state so pages re-render on sign-in / sign-out.
export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => auth.loadSession());

  // Drop the session the moment the access token expires.
  useEffect(() => {
    if (!session) return undefined;
    const id = window.setTimeout(() => setSession(auth.loadSession()), session.expiresAt - Date.now());
    return () => window.clearTimeout(id);
  }, [session]);

  const finishLogin = useCallback(async (search: string) => {
    const returnTo = await auth.completeLogin(search);
    const next = auth.loadSession();
    setSession(next);
    return returnTo || (isAdmin(next) ? "/admin" : "/console");
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      configured: MOCK || auth.cognitoConfigured,
      signIn: auth.beginLogin,
      finishLogin,
      mockSignIn: (username, groups) => {
        auth.mockSignIn(username, groups);
        setSession(auth.loadSession());
      },
      signOut: () => {
        auth.signOut();
        setSession(null);
      },
    }),
    [session, finishLogin],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

// For pages behind <RequireAuth>, where a session always exists.
export function useSession(): Session {
  const { session } = useAuth();
  if (!session) throw new Error("useSession must be used inside <RequireAuth>");
  return session;
}
