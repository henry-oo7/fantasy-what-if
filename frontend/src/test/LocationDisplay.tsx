import { useLocation } from "react-router-dom";

/** Test-only probe exposing the current router location. */
export function LocationDisplay() {
  const location = useLocation();

  return (
    <div data-testid="location" hidden>
      {location.pathname}
      {location.search}
    </div>
  );
}
