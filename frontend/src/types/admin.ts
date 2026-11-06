export type InvitationStatus =
  | "active"
  | "used"
  | "revoked";

export interface InvitationListItem {
  id: number;
  team_id: number;
  team_name: string;
  season: number;
  intended_display_name: string | null;
  status: InvitationStatus;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
}

export interface InvitationListResponse {
  invitations: InvitationListItem[];
}

export interface CreateInvitationRequest {
  team_id: number;
  intended_display_name: string | null;
}

export interface CreatedInvitation {
  id: number;
  team_id: number;
  team_name: string;
  season: number;
  intended_display_name: string | null;
  expires_at: string;
  registration_url: string;
}

export interface RevokedInvitation {
  id: number;
  status: "revoked";
  revoked_at: string;
}
