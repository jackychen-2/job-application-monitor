import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { authMe, logout, startGoogleLogin } from "../api/client";
import type { AuthState, AuthUser } from "../types";

interface AuthContextValue extends AuthState {
  refreshAuth: () => Promise<void>;
  loginWithGoogle: () => void;
  logoutUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<AuthUser | null>(null);

  const refreshAuth = useCallback(async () => {
    try {
      const me = await authMe();
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);

  const loginWithGoogle = useCallback(() => {
    startGoogleLogin();
  }, []);

  const logoutUser = useCallback(async () => {
    await logout();
    setUser(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      loading,
      user,
      refreshAuth,
      loginWithGoogle,
      logoutUser,
    }),
    [loading, user, refreshAuth, loginWithGoogle, logoutUser]
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
