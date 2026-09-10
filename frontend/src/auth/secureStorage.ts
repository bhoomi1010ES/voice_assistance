import { NativeModules, Platform } from 'react-native';
import { AuthTokens } from './types';
import {
  clearAuthTokens,
  readAuthTokens,
  storeAuthTokens,
} from '../native/VoiceModule';

export interface AuthTokenStorage {
  read(): Promise<AuthTokens | null>;
  save(tokens: AuthTokens): Promise<void>;
  clear(): Promise<void>;
}

export function createNativeAuthTokenStorage(): AuthTokenStorage {
  const nativeAvailable =
    Platform.OS === 'android' && Boolean(NativeModules.VoiceModule);

  if (!nativeAvailable) {
    return {
      async read() {
        return null;
      },
      async save() {
        throw new Error('Secure authentication storage is unavailable.');
      },
      async clear() {},
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
      await clearAuthTokens();
    },
  };
}
