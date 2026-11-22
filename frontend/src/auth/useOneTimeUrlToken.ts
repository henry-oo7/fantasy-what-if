import {
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
} from "react";
import {
  useLocation,
  useNavigate,
} from "react-router-dom";

function readToken(search: string, parameter: string): string | null {
  const value = new URLSearchParams(search).get(parameter);

  return value !== null && value.trim().length > 0 ? value.trim() : null;
}

export interface OneTimeUrlToken {
  /** Holds the token until the caller submits it, then is cleared. */
  tokenRef: MutableRefObject<string | null>;
  /** Whether the page was opened with a token (captured once). */
  hadToken: boolean;
}

/**
 * Capture a one-time token from the URL query and remove it immediately.
 *
 * The token is held only in a ref for the component that submits it. It
 * is never placed in React state, browser storage, logs, or rendered
 * output, and the visible URL is rewritten with history replacement so
 * the token does not stay in the address bar or browser history entry.
 */
export function useOneTimeUrlToken(
  parameter = "token",
): OneTimeUrlToken {
  const location = useLocation();
  const navigate = useNavigate();

  // useRef keeps only the first render's value; later renders, after the
  // URL is rewritten, do not overwrite it.
  const tokenRef = useRef<string | null>(
    readToken(location.search, parameter),
  );
  const [hadToken] = useState(
    () => readToken(location.search, parameter) !== null,
  );

  useEffect(() => {
    const search = new URLSearchParams(location.search);

    if (!search.has(parameter)) {
      return;
    }

    search.delete(parameter);
    const remaining = search.toString();

    navigate(
      {
        pathname: location.pathname,
        search: remaining ? `?${remaining}` : "",
        hash: location.hash,
      },
      {
        replace: true,
        state: null,
      },
    );
  }, [location.hash, location.pathname, location.search, navigate, parameter]);

  return { tokenRef, hadToken };
}
