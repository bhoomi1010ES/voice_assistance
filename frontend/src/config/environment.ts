export type PublicApiConfig = {
  httpBaseUrl: string;
  websocketBaseUrl: string;
};

type RuntimeGlobals = typeof globalThis & {
  __VOICE_API_BASE_URL__?: unknown;
};

// The development APK is tested on the physical device over the local Wi-Fi
// network. Keep the backend bound to 0.0.0.0 when using this address.
const DEVELOPMENT_HTTP_URL = 'http://192.168.1.9:8000';
const PRODUCTION_HTTP_URL = 'https://api.invalid';

function isDevelopment(): boolean {
  return typeof __DEV__ !== 'undefined' && __DEV__;
}

function normalizeBaseUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.trim().length === 0) {
    return null;
  }

  const candidate = value.trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^/\s]+(?:\/[^\s]*)?$/i.test(candidate)) {
    return null;
  }

  if (/^https?:\/\/[^/\s]*@/i.test(candidate)) {
    return null;
  }

  return candidate;
}

function toWebSocketUrl(httpUrl: string): string {
  return httpUrl.replace(/^http/i, 'ws');
}

export function getPublicApiConfig(
  runtime: RuntimeGlobals = globalThis as RuntimeGlobals,
): PublicApiConfig {
  const configuredUrl = normalizeBaseUrl(runtime.__VOICE_API_BASE_URL__);
  const httpBaseUrl =
    configuredUrl ??
    (isDevelopment() ? DEVELOPMENT_HTTP_URL : PRODUCTION_HTTP_URL);

  return {
    httpBaseUrl,
    websocketBaseUrl: toWebSocketUrl(httpBaseUrl),
  };
}

export const publicApiConfig = getPublicApiConfig();
