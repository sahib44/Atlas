import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext } from "react";

export const SessionScope = createContext("");
export const sessionQueryKey = ["/auth/me"] as const;

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export function isAccessError(error: unknown) {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}
export type Row = { id: string; [key: string]: any };
export type Page = {
  items: Row[];
  total: number;
  page: number;
  page_size: number;
};
export type Department = {
  slug: string;
  name: string;
  category_slug: string;
};
export type User = {
  id: string;
  full_name: string;
  email: string;
  role: string;
  permissions: string[];
  is_active: boolean;
  managed_account?: boolean;
  recovery_required?: boolean;
  department?: Department | null;
};
export type AccountMode = { managed: boolean; sms_available?: boolean };
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch("/api" + path, {
    method,
    signal,
    credentials: "same-origin",
    headers: {
      "X-MIST-Request": "1",
      ...(body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
    },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(
      error.detail?.message || `Request failed (${response.status})`,
      response.status,
    );
  }
  return response.json();
}

export function useSession() {
  return useQuery<User | null>({
    queryKey: sessionQueryKey,
    queryFn: async ({ signal }) => {
      try {
        return await api<User>("/auth/me", "GET", undefined, signal);
      } catch (error) {
        // Being signed out is a resolved session state, so a recheck keeps the form mounted.
        if (isAccessError(error)) return null;
        throw error;
      }
    },
    refetchInterval: query => query.state.data ? 3000 : false,
  });
}

export function useData<T = any>(path: string, enabled = true) {
  const scope = useContext(SessionScope);
  const cache = useQueryClient();
  const query = useQuery<T>({
    queryKey: [path, scope],
    queryFn: async ({ signal }) => {
      try {
        return await api<T>(path, "GET", undefined, signal);
      } catch (error) {
        if (isAccessError(error)) {
          void cache.invalidateQueries({ queryKey: sessionQueryKey }, { cancelRefetch: false });
        }
        throw error;
      }
    },
    enabled,
    retry: false,
    refetchInterval:
      ["/accounts", "/audit-events", "/recruitment", "/outbox", "/campaigns", "/notifications", "/conversations", "/competitions", "/judge-directory", "/dashboard", "/tickets", "/imports", "/duplicates", "/judges", "/recipient-search"].some(prefix => path.startsWith(prefix))
        ? 3000
        : false,
  });
  // A failed authorization check can leave the previous successful value in the cache.
  return isAccessError(query.error) || (query.error instanceof ApiError && query.error.status === 404)
    ? { ...query, data: undefined }
    : query;
}
export function useAction() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: ({
      path,
      method = "POST",
      body,
    }: {
      path: string;
      method?: string;
      body?: unknown;
    }) => api(path, method, body),
    onSuccess: (_result, variables) => {
      if (variables.path === "/auth/logout") {
        cache.clear();
        window.location.assign("/login");
        return;
      }
      return cache.invalidateQueries();
    },
    onError: (_error, variables) => {
      if (variables.path !== "/auth/login") {
        void cache.invalidateQueries(undefined, { cancelRefetch: false });
      }
    },
  });
}
const grants: Record<string, string[]> = {
  director: [
    "delete",
    "judge_assign",
    "recruitment_review",
    "ticket_approve",
    "judges",
    "imports",
    "campaigns",
    "campaign_cancel",
    "tickets",
    "catalogs",
    "audit",
    "users",
    "outbox",
    "jobs",
  ],
  technology_administrator: ["imports", "catalogs", "audit", "outbox", "jobs"],
};
for (const role of ["competitions_administrator", "competitions_member", "competitions_chair"]) {
  grants[role] = ["judges", "imports", "campaigns", "campaign_cancel", "tickets", "catalogs", "outbox"];
}
grants.competitions_chair.push("approve", "delete", "ticket_approve", "judge_assign", "recruitment_review");
export function can(user: User | undefined, permission: string) {
  if (permission === "manage_accounts") {
    return user?.managed_account === true && user.role === "competitions_chair";
  }
  return (
    !!user &&
    (grants[user.role]?.includes(permission) ||
      (permission === "tickets" &&
        user.permissions.includes("private_tickets")))
  );
}
export const label = (s: unknown) =>
  String(s === "waiting_on_organizer" ? "Awaiting organizer" : s ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
export function accountRoleLabel(user: User) {
  if (can(user, "manage_accounts")) return "Competitions Lead";
  if (user.department) return `${user.department.name} Head`;
  return label(user.role);
}
export const date = (s: string | undefined) =>
  s ? new Date(s).toLocaleString() : "—";

export function requestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(
    "",
  );
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function requestKey(ref: { current: { signature: string; id: string } | null }, payload: object) {
  const signature = JSON.stringify(payload);
  if (!ref.current || ref.current.signature !== signature) {
    ref.current = { signature, id: requestId() };
  }
  return ref.current.id;
}
