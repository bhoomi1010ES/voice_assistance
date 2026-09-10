export type AuthStatus = 'unknown' | 'authenticated' | 'unauthenticated';

export type AuthTokens = {
  accessToken: string;
  refreshToken: string;
};

export type UserProfile = {
  id: string;
  email: string;
  name: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  timezone?: string | null;
};

export type AuthSession = {
  id: string;
  device_id: string;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  revoked_at: string | null;
};

export type Device = {
  id: string;
  device_identifier: string;
  platform: string;
  name: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
  last_seen_at: string;
  revoked_at: string | null;
};

export type AuthState = {
  status: AuthStatus;
  profile: UserProfile | null;
  restoreError: string | null;
  sessionExpired: boolean;
};

export type AuthTokenResponse = {
  token_type: string;
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user: UserProfile;
  device: Device;
  session: AuthSession;
};
