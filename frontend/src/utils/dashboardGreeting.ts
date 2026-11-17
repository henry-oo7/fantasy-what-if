import type {
  UserRole,
} from "../types/auth";


interface GreetingUser {
  role: UserRole;
  display_name: string | null | undefined;
}


export function dashboardGreeting(
  user: GreetingUser | null | undefined,
): string {
  if (user?.role === "spectator") {
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
