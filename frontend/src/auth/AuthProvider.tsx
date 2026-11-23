import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import {
  getCurrentUser,
  loginAdministrator as requestAdministratorLogin,
  loginLegacyUsername as requestLegacyUsernameLogin,
  loginSpectator as requestSpectatorLogin,
  logout,
} from "../api/auth";
import {
  ApiError,
  subscribeToApiErrors,
} from "../api/client";
import { setupQueryKeys } from "../api/setup";
import type {
  AdministratorLoginRequest,
  AuthenticatedUser,
  AuthenticationNotice,
  LegacyUsernameLoginRequest,
  SpectatorLoginRequest,
  WorkspaceAccess,
} from "../types/auth";
import { workspaceAccessFor } from "./workspaceLifecycle";

type AuthenticationStatus =
  | "loading"
  | "authenticated"
  | "anonymous"
  | "error";

export interface EstablishedSession {
  user: AuthenticatedUser;
  workspaceAccess: WorkspaceAccess;
}

interface AuthenticationContextValue {
  status: AuthenticationStatus;
  user: AuthenticatedUser | null;
  csrfToken: string | null;
  error: string | null;
  workspaceAccess: WorkspaceAccess;
  notice: AuthenticationNotice | null;
  loginAdministrator: (
    request: AdministratorLoginRequest,
  ) => Promise<EstablishedSession>;
  loginSpectator: (
    request: SpectatorLoginRequest,
  ) => Promise<EstablishedSession>;
  /** Legacy single-installation setup bootstrap only. */
  loginLegacyUsername: (
    request: LegacyUsernameLoginRequest,
  ) => Promise<EstablishedSession>;
  /** Adopt the session cookie set by a successful email verification. */
  adoptVerifiedSession: () => Promise<EstablishedSession>;
  logoutUser: () => Promise<void>;
  /**
   * End a session the server has already removed (for example a losing
   * duplicate league claim), clearing state and cached data without a
   * logout request.
   */
  endSession: (notice?: AuthenticationNotice) => void;
  refreshUser: () => Promise<void>;
  showNotice: (notice: AuthenticationNotice) => void;
  clearNotice: () => void;
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

  return "Unable to verify your session. Check your connection and try again.";
}

/**
 * Query roots that hold public, unauthenticated data. Everything else is
 * treated as authenticated workspace data and removed when the session
 * ends or changes, so unclassified future keys are removed by default.
 */
const PUBLIC_QUERY_ROOTS: ReadonlySet<unknown> = new Set([
  setupQueryKeys.all[0],
]);

function purgeAuthenticatedQueries(queryClient: QueryClient): void {
  const isAuthenticatedQuery = (query: { queryKey: readonly unknown[] }) =>
    !PUBLIC_QUERY_ROOTS.has(query.queryKey[0]);

  // Cancel in-flight fetches so a late response cannot repopulate the
  // cache, then remove the data synchronously before any rerender.
  void queryClient.cancelQueries({ predicate: isAuthenticatedQuery });
  queryClient.removeQueries({ predicate: isAuthenticatedQuery });
  queryClient.getMutationCache().clear();
}

function identityKey(user: AuthenticatedUser): string {
  return `${user.role}:${user.id}`;
}

/**
 * Lifecycle change reported by a protected request after sign-in.
 *
 * Provisional onboarding is decided only by GET /api/auth/me, never
 * inferred from an error response.
 */
function workspaceAccessFromError(
  error: ApiError,
): "suspended" | "inactive" | null {
  if (error.status !== 403) {
    return null;
  }

  if (error.code === "workspace_suspended") {
    return "suspended";
  }

  if (error.code === "workspace_inactive") {
    return "inactive";
  }

  return null;
}

export function AuthProvider({
  children,
}: AuthenticationProviderProps) {
  const queryClient = useQueryClient();
  const [status, setStatus] =
    useState<AuthenticationStatus>("loading");
  const [user, setUser] =
    useState<AuthenticatedUser | null>(null);
  const [csrfToken, setCsrfToken] =
    useState<string | null>(null);
  const [error, setError] =
    useState<string | null>(null);
  const [workspaceAccess, setWorkspaceAccess] =
    useState<WorkspaceAccess>("checking");
  const [notice, setNotice] =
    useState<AuthenticationNotice | null>(null);

  const userRef = useRef<AuthenticatedUser | null>(null);
  // Identity whose data the query cache may currently hold.
  const cachedIdentityRef = useRef<string | null>(null);
  const workspaceAccessRef = useRef<WorkspaceAccess>("checking");

  const purgeCache = useCallback(() => {
    purgeAuthenticatedQueries(queryClient);
    cachedIdentityRef.current = null;
  }, [queryClient]);

  /**
   * Apply a workspace lifecycle result. Entering a blocked state removes
   * cached workspace data once; repeated identical results do nothing.
   */
  const applyWorkspaceAccess = useCallback(
    (access: WorkspaceAccess) => {
      const previous = workspaceAccessRef.current;
      workspaceAccessRef.current = access;

      if (
        (access === "suspended" || access === "inactive") &&
        previous !== access
      ) {
        purgeCache();
      }

      setWorkspaceAccess(access);
    },
    [purgeCache],
  );

  const clearSession = useCallback(() => {
    // Purge synchronously before state changes, so no rerender can show
    // the previous identity's workspace data.
    purgeCache();
    workspaceAccessRef.current = "checking";
    userRef.current = null;
    setUser(null);
    setCsrfToken(null);
    setError(null);
    setWorkspaceAccess("checking");
    setStatus("anonymous");
  }, [purgeCache]);

  const establishSession = useCallback(
    async (): Promise<EstablishedSession> => {
      const currentUser = await getCurrentUser();
      const nextIdentity = identityKey(currentUser);

      // A different identity must never see the previous identity's
      // cached data. Re-establishing the same identity keeps the cache.
      if (
        cachedIdentityRef.current !== null &&
        cachedIdentityRef.current !== nextIdentity
      ) {
        purgeCache();
      }

      cachedIdentityRef.current = nextIdentity;
      userRef.current = currentUser;

      // GET /api/auth/me is authoritative: no separate lifecycle probe.
      const access = workspaceAccessFor(currentUser);

      applyWorkspaceAccess(access);
      setUser(currentUser);
      setCsrfToken(readSessionCsrfToken());
      setError(null);
      setStatus("authenticated");

      return {
        user: currentUser,
        workspaceAccess: access,
      };
    },
    [applyWorkspaceAccess, purgeCache],
  );

  const refreshUser = useCallback(async () => {
    try {
      await establishSession();
    } catch (currentError) {
      if (
        currentError instanceof ApiError &&
        currentError.status === 401
      ) {
        clearSession();
        return;
      }

      userRef.current = null;
      setUser(null);
      setError(errorMessage(currentError));
      setStatus("error");
    }
  }, [clearSession, establishSession]);

  useEffect(() => {
    // Session discovery synchronizes React state with
    // the server-side authentication session.
    // oxlint-disable-next-line react/set-state-in-effect
    void refreshUser();
  }, [refreshUser]);

  useEffect(
    () =>
      subscribeToApiErrors((apiError) => {
        if (apiError.status === 401) {
          // The backend sends session_replaced only for this browser's
          // own replaced session, so show it even when the replacement
          // is discovered by the first /me after a reload. Public account
          // endpoints never report errors here.
          if (apiError.code === "session_replaced") {
            clearSession();
            setNotice("session_replaced");
            return;
          }

          if (userRef.current !== null) {
            clearSession();
          }

          return;
        }

        const access = workspaceAccessFromError(apiError);

        // A provisional administrator is already outside the active
        // application; an inactive response does not change that.
        if (
          access !== null &&
          userRef.current !== null &&
          !(access === "inactive" && workspaceAccessRef.current === "provisional")
        ) {
          applyWorkspaceAccess(access);
        }
      }),
    [applyWorkspaceAccess, clearSession],
  );

  const loginAdministrator = useCallback(
    async (
      request: AdministratorLoginRequest,
    ): Promise<EstablishedSession> => {
      await requestAdministratorLogin(request);
      setNotice(null);

      return establishSession();
    },
    [establishSession],
  );

  const loginSpectator = useCallback(
    async (
      request: SpectatorLoginRequest,
    ): Promise<EstablishedSession> => {
      await requestSpectatorLogin(request);
      setNotice(null);

      return establishSession();
    },
    [establishSession],
  );

  const loginLegacyUsername = useCallback(
    async (
      request: LegacyUsernameLoginRequest,
    ): Promise<EstablishedSession> => {
      await requestLegacyUsernameLogin(request);
      setNotice(null);

      return establishSession();
    },
    [establishSession],
  );

  const adoptVerifiedSession = useCallback(async () => {
    setNotice(null);

    return establishSession();
  }, [establishSession]);

  const logoutUser = useCallback(async () => {
    const currentCsrf =
      csrfToken ?? readSessionCsrfToken();

    try {
      if (currentCsrf) {
        await logout(currentCsrf);
      }
    } finally {
      setNotice(null);
      clearSession();
    }
  }, [clearSession, csrfToken]);

  const endSession = useCallback(
    (nextNotice?: AuthenticationNotice) => {
      // clearSession purges cached workspace data synchronously first.
      clearSession();
      setNotice(nextNotice ?? null);
    },
    [clearSession],
  );

  const showNotice = useCallback(
    (nextNotice: AuthenticationNotice) => {
      setNotice(nextNotice);
    },
    [],
  );

  const clearNotice = useCallback(() => {
    setNotice(null);
  }, []);

  const value = useMemo<AuthenticationContextValue>(
    () => ({
      status,
      user,
      csrfToken,
      error,
      workspaceAccess,
      notice,
      loginAdministrator,
      loginSpectator,
      loginLegacyUsername,
      adoptVerifiedSession,
      logoutUser,
      endSession,
      refreshUser,
      showNotice,
      clearNotice,
    }),
    [
      status,
      user,
      csrfToken,
      error,
      workspaceAccess,
      notice,
      loginAdministrator,
      loginSpectator,
      loginLegacyUsername,
      adoptVerifiedSession,
      logoutUser,
      endSession,
      refreshUser,
      showNotice,
      clearNotice,
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
