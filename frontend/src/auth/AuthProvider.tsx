import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  getCurrentUser,
  login,
  logout,
  requestPreAuthCsrf,
} from "../api/auth";
import { ApiError } from "../api/client";
import type {
  AuthenticatedUser,
  LoginRequest,
} from "../types/auth";

type AuthenticationStatus =
  | "loading"
  | "authenticated"
  | "anonymous"
  | "error";

interface AuthenticationContextValue {
  status: AuthenticationStatus;
  user: AuthenticatedUser | null;
  error: string | null;
  loginUser: (
    request: LoginRequest,
  ) => Promise<AuthenticatedUser>;
  logoutUser: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

interface AuthenticationProviderProps {
  children: ReactNode;
}

const AuthenticationContext =
  createContext<AuthenticationContextValue | null>(
    null,
  );

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;

  for (const entry of document.cookie.split(";")) {
    const cookie = entry.trim();

    if (cookie.startsWith(prefix)) {
      return decodeURIComponent(
        cookie.slice(prefix.length),
      );
    }
  }

  return null;
}

function readSessionCsrfToken(): string | null {
  return (
    readCookie("fantasy_session_csrf") ??
    readCookie("__Host-fantasy_csrf")
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "An unexpected authentication error occurred.";
}

export function AuthProvider({
  children,
}: AuthenticationProviderProps) {
  const [status, setStatus] =
    useState<AuthenticationStatus>("loading");
  const [user, setUser] =
    useState<AuthenticatedUser | null>(null);
  const [csrfToken, setCsrfToken] =
    useState<string | null>(null);
  const [error, setError] =
    useState<string | null>(null);

  const refreshUser = useCallback(async () => {
    try {
      const currentUser = await getCurrentUser();

      setUser(currentUser);
      setCsrfToken(readSessionCsrfToken());
      setError(null);
      setStatus("authenticated");
    } catch (currentError) {
      if (
        currentError instanceof ApiError &&
        currentError.status === 401
      ) {
        setUser(null);
        setCsrfToken(null);
        setError(null);
        setStatus("anonymous");
        return;
      }

      setUser(null);
      setError(errorMessage(currentError));
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    // Session discovery synchronizes React state with
    // the server-side authentication session.
    // oxlint-disable-next-line react/set-state-in-effect
    void refreshUser();
  }, [refreshUser]);

  const loginUser = useCallback(
    async (
      request: LoginRequest,
    ): Promise<AuthenticatedUser> => {
      const preAuthCsrf =
        await requestPreAuthCsrf();

      const response = await login(
        request,
        preAuthCsrf.csrf_token,
      );

      setUser(response.user);
      setCsrfToken(response.csrf_token);
      setError(null);
      setStatus("authenticated");

      return response.user;
    },
    [],
  );

  const logoutUser = useCallback(async () => {
    const currentCsrf =
      csrfToken ?? readSessionCsrfToken();

    if (!currentCsrf) {
      setUser(null);
      setStatus("anonymous");
      return;
    }

    try {
      await logout(currentCsrf);
    } finally {
      setUser(null);
      setCsrfToken(null);
      setError(null);
      setStatus("anonymous");
    }
  }, [csrfToken]);

  const value = useMemo<AuthenticationContextValue>(
    () => ({
      status,
      user,
      error,
      loginUser,
      logoutUser,
      refreshUser,
    }),
    [
      status,
      user,
      error,
      loginUser,
      logoutUser,
      refreshUser,
    ],
  );

  return (
    <AuthenticationContext.Provider value={value}>
      {children}
    </AuthenticationContext.Provider>
  );
}

// The provider and hook intentionally share one module.
// oxlint-disable-next-line react/only-export-components
export function useAuth(): AuthenticationContextValue {
  const context = useContext(
    AuthenticationContext,
  );

  if (context === null) {
    throw new Error(
      "useAuth must be used inside AuthProvider.",
    );
  }

  return context;
}
