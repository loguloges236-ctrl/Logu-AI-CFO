export type Owner = {
  id: string;
  email: string;
  default_currency: string;
  voice_unlock_enabled: boolean;
};

export type DocumentFile = {
  id: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  scan_status: "pending" | "scanning" | "clean" | "infected" | "failed";
  downloadable: boolean;
};

export type Document = {
  id: string;
  kind: string;
  status: "pending" | "active" | "quarantined" | "revoked";
  display_name: string;
  created_at: string;
  files: DocumentFile[];
};

export type SessionItem = {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  user_agent: string | null;
  current: boolean;
};

export type DeviceItem = {
  id: string;
  display_name: string | null;
  created_at: string;
  last_seen_at: string;
  revoked: boolean;
  current: boolean;
};

export type VoiceStatus = {
  enabled: boolean;
  implemented: boolean;
  provider_configured: boolean;
  status_message: string;
};
