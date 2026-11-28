import type {
  UserRole,
} from "../types/auth";


interface GreetingUser {
  legacy_workspace?: { role: UserRole } | null;
  display_name: string | null | undefined;
}


export function dashboardGreeting(
  user: GreetingUser | null | undefined,
): string {
  if (user?.legacy_workspace?.role === "spectator") {
    return "Welcome back, Spectator";
  }

  const firstName =
    user?.display_name
      ?.trim()
      .split(/\s+/u)
      .find(Boolean) ?? "";

  return firstName
    ? `Welcome back, ${firstName}`
    : "Welcome back";
}
