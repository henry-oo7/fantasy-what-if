import {
  Navigate,
} from "react-router-dom";

import { useAuth } from "./AuthProvider";


export function AdminRoute({
  children,
}: {
  children: React.ReactNode;
}) {
  const auth = useAuth();

  if (
    auth.status !== "authenticated" ||
    auth.user === null
  ) {
    return (
      <Navigate
        to="/login"
        replace
      />
    );
  }

  if (auth.user.role !== "admin") {
    return (
      <Navigate
        to="/"
        replace
      />
    );
  }

  return children;
}
