import { requestJson } from '../api/httpClient';
import { AuthTokenResponse, AuthSession, Device, UserProfile } from './types';

export type LoginDetails = {
  email: string;
  password: string;
};

export type RegisterDetails = {
  name: string;
  email: string;
  password: string;
};

export function registerRequest(
  details: RegisterDetails,
  fetchImpl: typeof fetch = fetch,
): Promise<UserProfile> {
  return requestJson<UserProfile>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({
      name: details.name,
      email: details.email,
      password: details.password,
    }),
    fetchImpl,
  });
}

export function loginRequest(
  details: LoginDetails,
  fetchImpl: typeof fetch = fetch,
): Promise<AuthTokenResponse> {
  return requestJson<AuthTokenResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({
      email: details.email,
      password: details.password,
      device_identifier: getDeviceIdentifier(),
      platform: 'android',
      device_name: 'Voice Assistant',
      device_kind: 'physical',
    }),
    fetchImpl,
  });
}

export function refreshRequest(
  refreshToken: string,
  fetchImpl: typeof fetch = fetch,
): Promise<AuthTokenResponse> {
  return requestJson<AuthTokenResponse>('/auth/refresh', {
    method: 'POST',
    body: JSON.stringify({ refresh_token: refreshToken }),
    fetchImpl,
  });
}

export function meRequest(
  accessToken: string,
  fetchImpl: typeof fetch = fetch,
): Promise<UserProfile> {
  return requestJson<UserProfile>('/auth/me', {
    method: 'GET',
    accessToken,
    fetchImpl,
  });
}

export function logoutRequest(
  accessToken: string,
  fetchImpl: typeof fetch = fetch,
): Promise<{ status: string }> {
  return requestJson<{ status: string }>('/auth/logout', {
    method: 'POST',
    accessToken,
    fetchImpl,
  });
}

export function sessionsRequest(
  accessToken: string,
  fetchImpl: typeof fetch = fetch,
): Promise<AuthSession[]> {
  return requestJson<AuthSession[]>('/auth/sessions', {
    method: 'GET',
    accessToken,
    fetchImpl,
  });
}

export function revokeSessionRequest(
  accessToken: string,
  sessionId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<{ status: string }> {
  return requestJson<{ status: string }>(`/auth/sessions/${sessionId}/revoke`, {
    method: 'POST',
    accessToken,
    fetchImpl,
  });
}

export function devicesRequest(
  accessToken: string,
  fetchImpl: typeof fetch = fetch,
): Promise<Device[]> {
  return requestJson<Device[]>('/devices', {
    method: 'GET',
    accessToken,
    fetchImpl,
  });
}

export function revokeDeviceRequest(
  accessToken: string,
  deviceId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<{ status: string }> {
  return requestJson<{ status: string }>(`/devices/${deviceId}/revoke`, {
    method: 'POST',
    accessToken,
    fetchImpl,
  });
}

function getDeviceIdentifier(): string {
  // The backend scopes device ownership to an account. A fresh opaque
  // identifier lets a second account sign in on the same physical device
  // after the first account signs out without reusing account A's device row.
  return `voice-assistant-android-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2)}`;
}
