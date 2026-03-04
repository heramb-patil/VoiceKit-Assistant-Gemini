/**
 * AuthContext — Google Sign-In session state for VoiceKit SaaS.
 *
 * Wraps @react-oauth/google and persists the ID token + user profile in
 * localStorage so the user stays logged in across page refreshes.
 *
 * Usage:
 *   const { user, idToken, signIn, signOut } = useAuth();
 */

import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from "react";
import { jwtDecode } from "jwt-decode";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AuthUser {
  email: string;
  name: string;
  picture: string;
  sub: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  idToken: string | null;
  signIn: (credentialResponse: { credential?: string }) => void;
  signOut: () => void;
  getIdToken: () => string | null;
}

// ── Storage keys ──────────────────────────────────────────────────────────────

const STORAGE_KEY_TOKEN = "voicekit_id_token";
const STORAGE_KEY_USER = "voicekit_user";

// ── Token expiry helpers ───────────────────────────────────────────────────────

function getTokenExp(token: string): number | null {
  try {
    const { exp } = jwtDecode<{ exp?: number }>(token);
    return exp ?? null;
  } catch {
    return null;
  }
}

function isTokenExpired(token: string): boolean {
  const exp = getTokenExp(token);
  return !exp || exp * 1000 < Date.now();
}

// ── Context ───────────────────────────────────────────────────────────────────

const AuthContext = createContext<AuthContextValue | null>(null);

function loadFromStorage(): { user: AuthUser | null; idToken: string | null } {
  try {
    const token = localStorage.getItem(STORAGE_KEY_TOKEN);
    const userJson = localStorage.getItem(STORAGE_KEY_USER);
    if (token && userJson) {
      if (isTokenExpired(token)) {
        // Expired at page load — clear so user sees login
        localStorage.removeItem(STORAGE_KEY_TOKEN);
        localStorage.removeItem(STORAGE_KEY_USER);
        return { user: null, idToken: null };
      }
      return { user: JSON.parse(userJson), idToken: token };
    }
  } catch {
    // corrupted storage — ignore
  }
  return { user: null, idToken: null };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const initial = loadFromStorage();
  const [user, setUser] = useState<AuthUser | null>(initial.user);
  const [idToken, setIdToken] = useState<string | null>(initial.idToken);

  const signIn = useCallback((credentialResponse: { credential?: string }) => {
    const token = credentialResponse.credential;
    if (!token) return;

    try {
      const decoded = jwtDecode<{
        email: string;
        name: string;
        picture: string;
        sub: string;
      }>(token);

      const authUser: AuthUser = {
        email: decoded.email,
        name: decoded.name ?? "",
        picture: decoded.picture ?? "",
        sub: decoded.sub,
      };

      setUser(authUser);
      setIdToken(token);
      localStorage.setItem(STORAGE_KEY_TOKEN, token);
      localStorage.setItem(STORAGE_KEY_USER, JSON.stringify(authUser));
    } catch (err) {
      console.error("[Auth] Failed to decode ID token:", err);
    }
  }, []);

  const signOut = useCallback(() => {
    setUser(null);
    setIdToken(null);
    localStorage.removeItem(STORAGE_KEY_TOKEN);
    localStorage.removeItem(STORAGE_KEY_USER);
  }, []);

  const getIdToken = useCallback((): string | null => {
    if (!idToken) return null;
    if (isTokenExpired(idToken)) {
      // Expired mid-session — clear state so AuthGate shows login
      setUser(null);
      setIdToken(null);
      localStorage.removeItem(STORAGE_KEY_TOKEN);
      localStorage.removeItem(STORAGE_KEY_USER);
      return null;
    }
    return idToken;
  }, [idToken]);

  // Auto sign-out timer: when the token expires, clear auth state so the
  // user sees the login page. This is a safety net — One Tap silent re-auth
  // (in App.tsx) should refresh the token before this fires.
  useEffect(() => {
    if (!idToken) return;
    const exp = getTokenExp(idToken);
    if (!exp) return;

    const msUntilExpiry = exp * 1000 - Date.now();
    if (msUntilExpiry <= 0) {
      signOut();
      return;
    }

    const timer = setTimeout(() => {
      console.warn('[Auth] Token expired — signing out');
      signOut();
    }, msUntilExpiry);

    return () => clearTimeout(timer);
  }, [idToken, signOut]);

  return (
    <AuthContext.Provider value={{ user, idToken, signIn, signOut, getIdToken }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
