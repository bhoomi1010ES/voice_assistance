import { NativeModules, Platform } from 'react-native';
import { AuthTokens } from './types';
import {
  clearAuthTokens,
  clearDeviceId as nativeClearDeviceId,
  readAuthTokens,
  readDeviceId as nativeReadDeviceId,
  storeAuthTokens,
  storeDeviceId as nativeStoreDeviceId,
} from '../native/VoiceModule';

export interface AuthTokenStorage {
  read(): Promise<AuthTokens | null>;
  save(tokens: AuthTokens): Promise<void>;
  clear(): Promise<void>;
  readDeviceId?(): Promise<string | null>;
  saveDeviceId?(deviceId: string): Promise<void>;
  clearDeviceId?(): Promise<void>;
}

export function createNativeAuthTokenStorage(): AuthTokenStorage {
  const nativeAvailable =
    Platform.OS === 'android' && Boolean(NativeModules.VoiceModule);

  let fallbackDeviceId: string | null = null;

  if (!nativeAvailable) {
    return {
      async read() {
        return null;
      },
      async save() {
        throw new Error('Secure authentication storage is unavailable.');
      },
      async clear() {
        fallbackDeviceId = null;
      },
      async readDeviceId() {
        return fallbackDeviceId;
      },
      async saveDeviceId(deviceId: string) {
        fallbackDeviceId = deviceId;
      },
      async clearDeviceId() {
        fallbackDeviceId = null;
      },
    };
  }

  return {
    async read() {
      return readAuthTokens();
    },
    async save(tokens) {
      await storeAuthTokens(tokens.accessToken, tokens.refreshToken);
    },
    async clear() {
      fallbackDeviceId = null;
      await Promise.allSettled([clearAuthTokens(), nativeClearDeviceId()]);
    },
    async readDeviceId() {
      try {
        return (await nativeReadDeviceId()) ?? fallbackDeviceId;
      } catch {
        return fallbackDeviceId;
      }
    },
    async saveDeviceId(deviceId: string) {
      fallbackDeviceId = deviceId;
      try {
        await nativeStoreDeviceId(deviceId);
      } catch {
        // Fallback retained in memory
      }
    },
    async clearDeviceId() {
      fallbackDeviceId = null;
      try {
        await nativeClearDeviceId();
      } catch {
        // Cleared fallback
      }
    },
  };
}
