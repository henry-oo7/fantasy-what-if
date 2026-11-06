import { useQuery } from "@tanstack/react-query";
import {
  CalendarDays,
  RefreshCw,
  ShieldCheck,
  Trophy,
  Users,
} from "lucide-react";

import {
  allPlayQueryOptions,
  currentLeagueQueryOptions,
} from "./api/league";
import { AuthenticatedUserMenu } from "./components/AuthenticatedUserMenu";
import "./App.css";

function App() {
  const leagueQuery = useQuery(currentLeagueQueryOptions());
  const allPlayQuery = useQuery(allPlayQueryOptions());

  if (leagueQuery.isPending || allPlayQuery.isPending) {
    return (
      <main className="app-state">
        <RefreshCw className="spinner" aria-hidden="true" />
        <p>Loading league dashboard...</p>
      </main>
    );
  }

  if (leagueQuery.isError || allPlayQuery.isError) {
    return (
      <main className="app-state">
        <h1>Unable to load the dashboard</h1>
        <p>
          Confirm that the FastAPI server is running on port 8000, then try
          again.
        </p>
        <button
          type="button"
          onClick={() => {
            void leagueQuery.refetch();
            void allPlayQuery.refetch();
          }}
        >
          <RefreshCw size={18} aria-hidden="true" />
          Retry
        </button>
      </main>
    );
  }

  const league = leagueQuery.data;
  const allPlay = allPlayQuery.data;
  const completedWeeks = Math.max(league.current_week - 1, 0);
  const completedMatchups = league.matchups.filter(
    (matchup) => matchup.week < league.current_week,
  ).length;
  const leader = allPlay[0];

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">ESPN Fantasy Football Analytics</p>
          <h1>{league.name}</h1>
          <p className="header-copy">
            Explore real standings, scoring performance, and all-play results
            across the {league.season} season.
          </p>
        </div>

        <div className="header-actions">
          <AuthenticatedUserMenu />

          <div className="season-badge">
          <Trophy size={20} aria-hidden="true" />
          <span>{league.season} Season</span>
          </div>
        </div>
      </header>

      <main className="dashboard">
        <section className="metric-grid" aria-label="League overview">
          <article className="metric-card">
            <div className="metric-icon metric-icon-blue">
              <CalendarDays aria-hidden="true" />
            </div>
            <div>
              <p>Current week</p>
              <strong>{league.current_week}</strong>
              <span>
                {completedWeeks} of {league.regular_season_weeks} completed
              </span>
            </div>
          </article>

          <article className="metric-card">
            <div className="metric-icon metric-icon-purple">
              <Users aria-hidden="true" />
            </div>
            <div>
              <p>League teams</p>
              <strong>{league.teams.length}</strong>
              <span>Active competitors</span>
            </div>
          </article>

          <article className="metric-card">
            <div className="metric-icon metric-icon-green">
              <ShieldCheck aria-hidden="true" />
            </div>
            <div>
              <p>Completed matchups</p>
              <strong>{completedMatchups}</strong>
              <span>{league.matchups.length} total scheduled</span>
            </div>
          </article>

          <article className="metric-card">
            <div className="metric-icon metric-icon-gold">
              <Trophy aria-hidden="true" />
            </div>
            <div>
              <p>All-play leader</p>
              <strong className="leader-name">
                {leader?.team_name ?? "Unavailable"}
              </strong>
              <span>
                {leader
                  ? `${leader.wins}-${leader.losses}-${leader.ties}`
                  : "No completed results"}
              </span>
            </div>
          </article>
        </section>

        <section className="content-card welcome-card">
          <div>
            <p className="section-label">Dashboard foundation</p>
            <h2>League data is connected</h2>
            <p>
              The frontend is now receiving live league and all-play data from
              FastAPI. The next step will add the interactive combined
              standings table.
            </p>
          </div>

          <div
            className="progress-panel"
            aria-label={`${completedWeeks} of ${league.regular_season_weeks} regular-season weeks completed`}
          >
            <div className="progress-heading">
              <span>Regular-season progress</span>
              <strong>
                {completedWeeks}/{league.regular_season_weeks}
              </strong>
            </div>

            <div className="progress-track">
              <div
                className="progress-value"
                style={{
                  width: `${
                    (completedWeeks / league.regular_season_weeks) * 100
                  }%`,
                }}
              />
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;