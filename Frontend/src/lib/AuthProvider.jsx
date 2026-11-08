import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import * as auth from "./auth.js";
import { MOCK } from "./mode.js";
import { isAdmin } from "./roles.js";

const AuthContext = createContext(null);

// Keeps the Cognito session (see auth.js) in React state so pages re-render on sign-in / sign-out.
export function AuthProvider({ children }) {
  const [session, setSession] = useState(() => auth.loadSession());

  // Drop the session the moment the access token expires.
  useEffect(() => {
    if (!session) return undefined;
    const id = window.setTimeout(() => setSession(auth.loadSession()), session.expiresAt - Date.now());
    return () => window.clearTimeout(id);
  }, [session]);

  const finishLogin = useCallback(async (search) => {
    const returnTo = await auth.completeLogin(search);
    const next = auth.loadSession();
    setSession(next);
    return returnTo || (isAdmin(next) ? "/admin" : "/console");
  }, []);

  const value = useMemo(
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

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
