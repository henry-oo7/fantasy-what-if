import {
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Check,
  ChevronDown,
  History,
  RefreshCw,
} from "lucide-react";
import {
  useQuery,
} from "@tanstack/react-query";

import {
  importedSeasonsQueryOptions,
} from "../../api/league";


interface SeasonMenuProps {
  onNavigate?: () => void;
}


export function SeasonMenu({
  onNavigate,
}: SeasonMenuProps) {
  const containerRef =
    useRef<HTMLDivElement | null>(null);

  const [open, setOpen] =
    useState(false);

  const seasonsQuery = useQuery(
    importedSeasonsQueryOptions(),
  );

  const activeSeason =
    seasonsQuery.data?.seasons.find(
      (season) => season.is_active,
    ) ??
    seasonsQuery.data?.seasons[0] ??
    null;

  useEffect(() => {
    if (!open) {
      return;
    }

    function handlePointerDown(
      event: PointerEvent,
    ) {
      const container = containerRef.current;

      if (
        container !== null &&
        event.target instanceof Node &&
        !container.contains(event.target)
      ) {
        setOpen(false);
      }
    }

    function handleKeyDown(
      event: KeyboardEvent,
    ) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    document.addEventListener(
      "pointerdown",
      handlePointerDown,
    );

    document.addEventListener(
      "keydown",
      handleKeyDown,
    );

    return () => {
      document.removeEventListener(
        "pointerdown",
        handlePointerDown,
      );

      document.removeEventListener(
        "keydown",
        handleKeyDown,
      );
    };
  }, [open]);

  function closeMenu() {
    setOpen(false);
    onNavigate?.();
  }

  return (
    <div
      ref={containerRef}
      className="season-menu"
    >
      <button
        type="button"
        className="sidebar-league season-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="imported-season-menu"
        onClick={() => {
          setOpen((current) => !current);
        }}
      >
        <span className="season-menu-current">
          <strong>
            {activeSeason?.league_name ??
              "Fantasy league"}
          </strong>

          <small>
            {activeSeason
              ? `${activeSeason.year} season`
              : "No imported season"}
          </small>
        </span>

        <ChevronDown
          className={
            open
              ? "season-menu-chevron season-menu-chevron-open"
              : "season-menu-chevron"
          }
          aria-hidden="true"
        />
      </button>

      {open ? (
        <div
          id="imported-season-menu"
          className="season-menu-popover"
          role="menu"
          aria-label="Imported seasons"
        >
          <div className="season-menu-heading">
            <div>
              <strong>Imported seasons</strong>

              <span>
                Select league history to view
              </span>
            </div>

            <History aria-hidden="true" />
          </div>

          {seasonsQuery.isPending ? (
            <div
              className="season-menu-state"
              aria-live="polite"
            >
              <RefreshCw
                className="spinner"
                aria-hidden="true"
              />

              Loading seasons...
            </div>
          ) : null}

          {seasonsQuery.isError ? (
            <div
              className="season-menu-state season-menu-error"
              role="alert"
            >
              <span>
                Unable to load imported seasons.
              </span>

              <button
                type="button"
                onClick={() => {
                  void seasonsQuery.refetch();
                }}
              >
                Try again
              </button>
            </div>
          ) : null}

          {seasonsQuery.data?.seasons.map(
            (season) => (
              <button
                key={season.season_id}
                type="button"
                role="menuitem"
                className={
                  season.is_active
                    ? "season-menu-option season-menu-option-active"
                    : "season-menu-option"
                }
                disabled={!season.is_active}
                title={
                  season.is_active
                    ? "Currently active season"
                    : "Historical season browsing will be enabled after season-aware analytics are added."
                }
                onClick={() => {
                  closeMenu();
                }}
              >
                <span>
                  <strong>
                    {season.league_name}
                  </strong>

                  <small>
                    {season.year} season
                  </small>
                </span>

                {season.is_active ? (
                  <span className="season-active-label">
                    <Check aria-hidden="true" />
                    Active
                  </span>
                ) : (
                  <span className="season-history-label">
                    Imported
                  </span>
                )}
              </button>
            ),
          )}

          {seasonsQuery.data?.seasons.length ===
          1 ? (
            <p className="season-menu-note">
              Additional imported seasons will appear
              here automatically.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
