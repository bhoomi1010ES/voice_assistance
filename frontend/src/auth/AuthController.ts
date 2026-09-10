import { ClientError, safeUserMessage, toClientError } from '../api/errors';
import { requestJson } from '../api/httpClient';
import {
  cancelVoiceResponse,
  disconnectVoiceGateway,
  endVoiceSession,
  stopMicrophone,
} from '../native/VoiceModule';
import {
  loginRequest,
  logoutRequest,
  refreshRequest,
  registerRequest,
} from './authApi';
import {
  createNativeAuthTokenStorage,
  AuthTokenStorage,
} from './secureStorage';
import {
  AuthState,
  AuthTokenResponse,
  AuthTokens,
  Device,
  AuthSession,
  UserProfile,
} from './types';

export type AuthControllerOptions = {
  storage?: AuthTokenStorage;
  fetchImpl?: typeof fetch;
  stopVoiceResources?: () => Promise<void>;
};

export type AuthListener = (state: AuthState) => void;

export class AuthController {
  private readonly storage: AuthTokenStorage;
  private readonly fetchImpl: typeof fetch;
  private readonly stopVoiceResourcesImpl: () => Promise<void>;
  private readonly listeners = new Set<AuthListener>();
  private tokens: AuthTokens | null = null;
  private refreshPromise: Promise<AuthTokens> | null = null;
  private state: AuthState = {
    status: 'unknown',
    profile: null,
    restoreError: null,
    sessionExpired: false,
  };

  constructor(options: AuthControllerOptions = {}) {
    this.storage = options.storage ?? createNativeAuthTokenStorage();
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.stopVoiceResourcesImpl =
      options.stopVoiceResources ?? stopVoiceResources;
  }

  subscribe(listener: AuthListener): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => this.listeners.delete(listener);
  }

  getState(): AuthState {
    return this.state;
  }

  async restore(): Promise<void> {
    this.setState({ status: 'unknown', restoreError: null });
    try {
      const storedTokens = await this.storage.read();
      if (!storedTokens) {
        this.tokens = null;
        this.setState({ status: 'unauthenticated', profile: null });
        return;
      }

      this.tokens = storedTokens;
      const profile = await this.request<UserProfile>('/auth/me');
      this.setState({
        status: 'authenticated',
        profile,
        sessionExpired: false,
      });
    } catch (error) {
      const clientError = toClientError(error);
      if (this.state.status === 'unauthenticated') {
        return;
      }
      this.tokens = null;
      if (clientError.status === 401 || clientError.status === 403) {
        await this.clearStorage();
        this.setState({ status: 'unauthenticated', profile: null });
        return;
      }
      this.setState({
        status: 'unknown',
        restoreError: safeUserMessage(clientError),
      });
    }
  }

  async login(email: string, password: string): Promise<void> {
    const response = await loginRequest({ email, password }, this.fetchImpl);
    await this.persistResponse(response);
    this.setState({
      status: 'authenticated',
      profile: response.user,
      restoreError: null,
      sessionExpired: false,
    });
  }

  async register(name: string, email: string, password: string): Promise<void> {
    await registerRequest({ name, email, password }, this.fetchImpl);
  }

  async updateProfile(name: string): Promise<UserProfile> {
    const profile = await this.request<UserProfile>('/auth/me', {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    });
    this.setState({ profile });
    return profile;
  }

  async logout(): Promise<void> {
    const accessToken = this.tokens?.accessToken;
    await this.stopVoiceResourcesImpl();
    if (accessToken) {
      try {
        await logoutRequest(accessToken, this.fetchImpl);
      } catch {
        // Local logout must complete even when the server is unavailable.
      }
    }
    this.tokens = null;
    await this.clearStorage();
    this.setState({
      status: 'unauthenticated',
      profile: null,
      restoreError: null,
      sessionExpired: false,
    });
  }

  dismissSessionExpired(): void {
    this.setState({ sessionExpired: false });
  }

  async listSessions(): Promise<AuthSession[]> {
    return this.request('/auth/sessions');
  }

  async revokeSession(sessionId: string): Promise<void> {
    await this.request(`/auth/sessions/${sessionId}/revoke`, {
      method: 'POST',
    });
  }

  async listDevices(): Promise<Device[]> {
    return this.request('/devices');
  }

  async revokeDevice(deviceId: string): Promise<void> {
    await this.request(`/devices/${deviceId}/revoke`, { method: 'POST' });
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const accessToken = this.tokens?.accessToken;
    if (!accessToken) {
      throw new ClientError('No authenticated session is available.', {
        kind: 'http',
        code: 'AUTHENTICATION_REQUIRED',
        status: 401,
      });
    }

    try {
      return await this.authenticatedRequest<T>(path, init, accessToken);
    } catch (error) {
      const clientError = toClientError(error);
      if (
        clientError.status !== 401 ||
        path === '/auth/refresh' ||
        path === '/auth/login' ||
        !isSafeRetryMethod(init.method)
      ) {
        throw clientError;
      }

      const refreshedTokens = await this.refreshAccessToken();
      return this.authenticatedRequest<T>(
        path,
        init,
        refreshedTokens.accessToken,
      );
    }
  }

  private async authenticatedRequest<T>(
    path: string,
    init: RequestInit,
    accessToken: string,
  ): Promise<T> {
    return requestJson<T>(path, {
      ...init,
      accessToken,
      fetchImpl: this.fetchImpl,
    });
  }

  private async refreshAccessToken(): Promise<AuthTokens> {
    if (this.refreshPromise) {
      return this.refreshPromise;
    }

    const refreshToken = this.tokens?.refreshToken;
    if (!refreshToken) {
      await this.invalidateSession(true);
      throw new ClientError('No refresh session is available.', {
        kind: 'http',
        code: 'REFRESH_UNAVAILABLE',
        status: 401,
      });
    }

    this.refreshPromise = refreshRequest(refreshToken, this.fetchImpl)
      .then(async response => {
        await this.persistResponse(response);
        return {
          accessToken: response.access_token,
          refreshToken: response.refresh_token,
        };
      })
      .catch(async error => {
        const clientError = toClientError(error);
        if (clientError.status === 401 || clientError.status === 403) {
          await this.invalidateSession(true);
        }
        throw clientError;
      })
      .finally(() => {
        this.refreshPromise = null;
      });

    return this.refreshPromise;
  }

  private async persistResponse(response: AuthTokenResponse): Promise<void> {
    const tokens = {
      accessToken: response.access_token,
      refreshToken: response.refresh_token,
    };
    await this.storage.save(tokens);
    this.tokens = tokens;
  }

  private async invalidateSession(sessionExpired: boolean): Promise<void> {
    this.tokens = null;
    await this.clearStorage();
    this.setState({
      status: 'unauthenticated',
      profile: null,
      restoreError: null,
      sessionExpired,
    });
  }

  private async clearStorage(): Promise<void> {
    try {
      await this.storage.clear();
    } catch {
      // State is still cleared if native storage is unavailable.
    }
  }

  private setState(patch: Partial<AuthState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach(listener => listener(this.state));
  }
}

async function stopVoiceResources(): Promise<void> {
  await Promise.allSettled([
    cancelVoiceResponse('logout'),
    endVoiceSession('logout'),
    disconnectVoiceGateway(),
    stopMicrophone(),
  ]);
}

function isSafeRetryMethod(method: string | undefined): boolean {
  return ['GET', 'HEAD', 'OPTIONS'].includes((method ?? 'GET').toUpperCase());
}
