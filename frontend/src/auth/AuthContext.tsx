import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { authMe, deleteAccount, logout, startGoogleLogin } from "../api/client";
import type { AuthState, AuthUser } from "../types";

interface AuthContextValue extends AuthState {
  refreshAuth: () => Promise<void>;
  loginWithGoogle: () => void;
  logoutUser: () => Promise<void>;
  deleteAccountUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);
const SESSION_HINT_COOKIE = "job_monitor_session_hint";

function hasSessionHint(): boolean {
  return document.cookie
    .split(";")
    .some((cookie) => cookie.trim().startsWith(`${SESSION_HINT_COOKIE}=`));
}

function clearSessionHint(): void {
  document.cookie = `${SESSION_HINT_COOKIE}=; Max-Age=0; Path=/; SameSite=Lax`;
}

function setSessionHint(): void {
  document.cookie = `${SESSION_HINT_COOKIE}=1; Path=/; SameSite=Lax`;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const startupCheckedRef = useRef(false);
  const [loading, setLoading] = useState(() => hasSessionHint());
  const [user, setUser] = useState<AuthUser | null>(null);

  const refreshAuth = useCallback(async () => {
    if (!hasSessionHint()) {
      setUser(null);
      setLoading(false);
      return;
    }

    setLoading(true);
    try {
      const me = await authMe();
      setUser(me);
      setSessionHint();
    } catch {
      setUser(null);
      clearSessionHint();
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (startupCheckedRef.current) {
      return;
    }
    startupCheckedRef.current = true;

    if (hasSessionHint()) {
      void refreshAuth();
      return;
    }

    setLoading(false);
    void (async () => {
      try {
        const me = await authMe();
        setUser(me);
        setSessionHint();
      } catch {
        setUser(null);
      }
    })();
  }, [refreshAuth]);

  const loginWithGoogle = useCallback(() => {
    startGoogleLogin();
  }, []);

  const logoutUser = useCallback(async () => {
    await logout();
    setUser(null);
    clearSessionHint();
  }, []);

  const deleteAccountUser = useCallback(async () => {
    await deleteAccount();
    setUser(null);
    clearSessionHint();
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      loading,
      user,
      refreshAuth,
      loginWithGoogle,
      logoutUser,
      deleteAccountUser,
    }),
    [loading, user, refreshAuth, loginWithGoogle, logoutUser, deleteAccountUser]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
