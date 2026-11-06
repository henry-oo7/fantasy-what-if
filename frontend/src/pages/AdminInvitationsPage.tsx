import {
  useMemo,
  useState,
  type FormEvent,
} from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Check,
  Clipboard,
  Link2,
  RefreshCw,
  UserPlus,
} from "lucide-react";
import { Navigate } from "react-router-dom";

import {
  adminQueryKeys,
  createInvitation,
  invitationListQueryOptions,
  revokeInvitation,
} from "../api/admin";
import { ApiError } from "../api/client";
import {
  currentLeagueQueryOptions,
} from "../api/league";
import { useAuth } from "../auth/AuthProvider";
import type {
  CreatedInvitation,
  InvitationListItem,
} from "../types/admin";
import "./AdminInvitationsPage.css";

function messageFromError(
  error: unknown,
): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "The invitation request failed.";
}

function effectiveStatus(
  invitation: InvitationListItem,
): "active" | "expired" | "used" | "revoked" {
  if (invitation.status !== "active") {
    return invitation.status;
  }

  if (
    new Date(invitation.expires_at).getTime() <=
    Date.now()
  ) {
    return "expired";
  }

  return "active";
}

export function AdminInvitationsPage() {
  const auth = useAuth();
  const queryClient = useQueryClient();

  const [teamId, setTeamId] = useState("");
  const [displayName, setDisplayName] =
    useState("");
  const [created, setCreated] =
    useState<CreatedInvitation | null>(null);
  const [copied, setCopied] =
    useState(false);

  const leagueQuery = useQuery(
    currentLeagueQueryOptions(),
  );
  const invitationsQuery = useQuery(
    invitationListQueryOptions(),
  );

  const teams = useMemo(
    () =>
      [...(leagueQuery.data?.teams ?? [])].sort(
        (first, second) =>
          first.name.localeCompare(second.name),
      ),
    [leagueQuery.data?.teams],
  );

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!auth.csrfToken) {
        throw new Error(
          "The session CSRF token is unavailable.",
        );
      }

      return createInvitation(
        {
          team_id: Number(teamId),
          intended_display_name:
            displayName.trim() || null,
        },
        auth.csrfToken,
      );
    },
    onSuccess: async (invitation) => {
      setCreated(invitation);
      setCopied(false);
      setTeamId("");
      setDisplayName("");

      await queryClient.invalidateQueries({
        queryKey: adminQueryKeys.invitations(),
      });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: async (
      invitationId: number,
    ) => {
      if (!auth.csrfToken) {
        throw new Error(
          "The session CSRF token is unavailable.",
        );
      }

      return revokeInvitation(
        invitationId,
        auth.csrfToken,
      );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: adminQueryKeys.invitations(),
      });
    },
  });

  if (
    auth.user !== null &&
    auth.user.role !== "admin"
  ) {
    return (
      <Navigate
        to="/"
        replace
      />
    );
  }

  function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    if (!teamId) {
      return;
    }

    createMutation.mutate();
  }

  async function copyRegistrationUrl() {
    if (!created) {
      return;
    }

    await navigator.clipboard.writeText(
      created.registration_url,
    );

    setCopied(true);
  }

  if (
    leagueQuery.isPending ||
    invitationsQuery.isPending
  ) {
    return (
      <section className="app-state">
        <RefreshCw
          className="spinner"
          aria-hidden="true"
        />
        <h1>Loading invitation management</h1>
      </section>
    );
  }

  return (
    <div className="admin-page">
      <header className="admin-page-header">
        <p className="page-kicker">
          League administration
        </p>

        <h1>Registration Invitations</h1>

        <p>
          Create single-use registration links
          assigned to specific fantasy teams.
        </p>
      </header>

      <div className="admin-invitation-layout">
        <section className="admin-panel">
          <div className="admin-panel-heading">
            <UserPlus aria-hidden="true" />

            <div>
              <h2>Create invitation</h2>
              <p>
                Links expire after seven days and
                can only be used once.
              </p>
            </div>
          </div>

          <form
            className="admin-invitation-form"
            onSubmit={(event) => {
              handleSubmit(event);
            }}
          >
            <div className="form-field">
              <label htmlFor="invited-team">
                Assigned fantasy team
              </label>

              <select
                id="invited-team"
                required
                value={teamId}
                onChange={(event) => {
                  setTeamId(event.target.value);
                }}
              >
                <option value="">
                  Select a team
                </option>

                {teams.map((team) => (
                  <option
                    key={team.team_id}
                    value={team.team_id}
                  >
                    {team.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-field">
              <label htmlFor="intended-name">
                Intended display name
                <span className="optional-label">
                  Optional
                </span>
              </label>

              <input
                id="intended-name"
                type="text"
                maxLength={100}
                value={displayName}
                onChange={(event) => {
                  setDisplayName(
                    event.target.value,
                  );
                }}
              />

              <small>
                When supplied, the invitee cannot
                change this name during registration.
              </small>
            </div>

            {createMutation.isError ? (
              <div
                className="form-error"
                role="alert"
              >
                {messageFromError(
                  createMutation.error,
                )}
              </div>
            ) : null}

            <button
              className="admin-primary-button"
              type="submit"
              disabled={
                !teamId ||
                createMutation.isPending
              }
            >
              <Link2 aria-hidden="true" />

              {createMutation.isPending
                ? "Creating..."
                : "Create registration link"}
            </button>
          </form>
        </section>

        <section className="admin-panel created-link-panel">
          <div className="admin-panel-heading">
            <Clipboard aria-hidden="true" />

            <div>
              <h2>New registration link</h2>
              <p>
                The raw invitation token is only
                displayed immediately after creation.
              </p>
            </div>
          </div>

          {created ? (
            <div className="created-link-result">
              <div className="created-link-team">
                <span>Assigned team</span>
                <strong>{created.team_name}</strong>
                <small>
                  Expires{" "}
                  {new Date(
                    created.expires_at,
                  ).toLocaleString()}
                </small>
              </div>

              <textarea
                aria-label="Registration URL"
                readOnly
                rows={4}
                value={created.registration_url}
              />

              <button
                className="admin-secondary-button"
                type="button"
                onClick={() => {
                  void copyRegistrationUrl();
                }}
              >
                {copied ? (
                  <Check aria-hidden="true" />
                ) : (
                  <Clipboard aria-hidden="true" />
                )}

                {copied
                  ? "Copied"
                  : "Copy private link"}
              </button>

              <p className="created-link-warning">
                Send this link privately. Do not post
                the link in a public channel.
              </p>
            </div>
          ) : (
            <div className="admin-empty-state">
              <Link2 aria-hidden="true" />
              <p>
                Create an invitation to display its
                one-time registration link here.
              </p>
            </div>
          )}
        </section>
      </div>

      <section className="admin-panel invitation-history">
        <div className="admin-panel-heading">
          <Clipboard aria-hidden="true" />

          <div>
            <h2>Invitation history</h2>
            <p>
              Review active, used, expired, and
              revoked invitations.
            </p>
          </div>
        </div>

        <div className="invitation-list">
          {invitationsQuery.data?.invitations
            .length ? (
            invitationsQuery.data.invitations.map(
              (invitation) => {
                const status =
                  effectiveStatus(invitation);

                return (
                  <article
                    key={invitation.id}
                    className="invitation-row"
                  >
                    <div className="invitation-row-team">
                      <strong>
                        {invitation.team_name}
                      </strong>

                      <span>
                        {invitation.intended_display_name ??
                          "No assigned display name"}
                      </span>
                    </div>

                    <span
                      className={`invitation-status invitation-status-${status}`}
                    >
                      {status}
                    </span>

                    <div className="invitation-date">
                      <span>Expires</span>
                      <strong>
                        {new Date(
                          invitation.expires_at,
                        ).toLocaleDateString()}
                      </strong>
                    </div>

                    {status === "active" ? (
                      <button
                        className="revoke-button"
                        type="button"
                        disabled={
                          revokeMutation.isPending
                        }
                        onClick={() => {
                          revokeMutation.mutate(
                            invitation.id,
                          );
                        }}
                      >
                        Revoke
                      </button>
                    ) : (
                      <span className="invitation-complete">
                        Complete
                      </span>
                    )}
                  </article>
                );
              },
            )
          ) : (
            <div className="admin-empty-state">
              <p>No invitations have been created.</p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
