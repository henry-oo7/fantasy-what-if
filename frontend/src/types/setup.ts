export type InstallationStatus =
  | "setup_required"
  | "setup_in_progress"
  | "active";

export interface SetupStatus {
  status: InstallationStatus;
  installation_exists: boolean;
  bootstrap_available: boolean;
}

export interface BootstrapAdminRequest {
  username: string;
  display_name: string;
  email: string | null;
  password: string;
}

export interface BootstrapAdminResponse {
  installation_status: "setup_in_progress";
  administrator_id: number;
  username: string;
  display_name: string;
  email: string | null;
}
