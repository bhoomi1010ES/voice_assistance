import { publicApiConfig } from '../config/environment';
import { ClientError } from './errors';

export type RequestOptions = RequestInit & {
  accessToken?: string;
  fetchImpl?: typeof fetch;
};

export async function requestJson<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { accessToken, fetchImpl = fetch, ...requestInit } = options;
  const headers = new Headers(requestInit.headers);
  headers.set('Accept', 'application/json');
  if (requestInit.body !== undefined) {
    headers.set('Content-Type', 'application/json');
  }
  if (accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`);
  }

  let response: Response;
  try {
    response = await fetchImpl(`${publicApiConfig.httpBaseUrl}${path}`, {
      ...requestInit,
      headers,
    });
  } catch (error) {
    throw new ClientError('A network request could not be completed.', {
      kind: 'network',
      code: 'NETWORK_UNAVAILABLE',
      retryable: true,
      cause: error,
    });
  }

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail = getDetail(payload);
    throw new ClientError('The server rejected the request.', {
      kind: 'http',
      code: detail.code ?? `HTTP_${response.status}`,
      status: response.status,
      retryable: response.status >= 500 || response.status === 429,
    });
  }

  return payload as T;
}

function getDetail(payload: unknown): { code?: string } {
  if (!payload || typeof payload !== 'object') {
    return {};
  }
  const detail = (payload as { detail?: unknown }).detail;
  if (!detail || typeof detail !== 'object') {
    return {};
  }
  const code = (detail as { code?: unknown }).code;
  return typeof code === 'string' ? { code } : {};
}
