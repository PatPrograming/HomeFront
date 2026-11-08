import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  autoSignIn,
  confirmSignUp,
  fetchAuthSession,
  resendSignUpCode,
  signIn,
  signOut,
  signUp,
} from "aws-amplify/auth";
import { Hub } from "aws-amplify/utils";
import { cognitoConfigured } from "./cognito.js";

const AuthContext = createContext(null);

// The session is the Cognito ID token, decoded. Amplify stores the tokens and
// refreshes them itself; Hub events tell us when they change.
async function loadSession() {
  if (!cognitoConfigured) return null;
  try {
    const { tokens } = await fetchAuthSession();
    const idToken = tokens?.idToken;
    if (!idToken) return null;
    const p = idToken.payload;
    return { user: { id: p.sub, email: p.email, name: p.name } };
  } catch {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setSession(await loadSession());
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
    const stop = Hub.listen("auth", ({ payload }) => {
      if (["signedIn", "signedOut", "tokenRefresh", "tokenRefresh_failure"].includes(payload.event)) {
        refresh();
      }
    });
    return stop;
  }, [refresh]);

  const value = useMemo(
    () => ({
      session,
      user: session?.user ?? null,
      loading,
      configured: cognitoConfigured,
      // Re-read the session now (e.g. right after sign-in) instead of waiting for Hub.
      refresh,
      // All of these throw on failure (Amplify errors carry a readable .message).
      // Returns { isSignedIn, nextStep }; nextStep.signInStep === "CONFIRM_SIGN_UP"
      // means the account exists but the email code was never entered.
      signIn: (email, password) => signIn({ username: email, password }),
      // Returns { nextStep }; "CONFIRM_SIGN_UP" = a code was emailed.
      signUp: (email, password, fullName) =>
        signUp({
          username: email,
          password,
          options: {
            userAttributes: { email, ...(fullName ? { name: fullName } : {}) },
            autoSignIn: true,
          },
        }),
      // Confirms the emailed code, then signs in when the sign-up started in
      // this tab. Returns true if the user ends up signed in.
      confirmSignUp: async (email, code) => {
        const { nextStep } = await confirmSignUp({ username: email, confirmationCode: code });
        if (nextStep.signUpStep !== "COMPLETE_AUTO_SIGN_IN") return false;
        try {
          const res = await autoSignIn();
          return res.isSignedIn;
        } catch {
          return false; // e.g. page reloaded since sign-up: sign in normally
        }
      },
      resendCode: (email) => resendSignUpCode({ username: email }),
      signOut: () => signOut(),
    }),
    [session, loading, refresh]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

// Bearer token for the backend: the current ID token (refreshed if expired).
export async function getIdToken() {
  if (!cognitoConfigured) return null;
  try {
    const { tokens } = await fetchAuthSession();
    return tokens?.idToken?.toString() ?? null;
  } catch {
    return null;
  }
}
