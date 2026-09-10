import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { BootstrapDependency } from '../app/bootstrap';
import { AuthController } from './AuthController';
import { AuthState } from './types';

type AuthContextValue = AuthState & {
  controller: AuthController;
  retryRestore: () => void;
  clearSessionExpired: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({
  children,
  controller,
  bootstrap,
}: {
  children: React.ReactNode;
  controller?: AuthController;
  bootstrap?: BootstrapDependency;
}) {
  const activeController = useMemo(
    () => controller ?? new AuthController(),
    [controller],
  );
  const [state, setState] = useState<AuthState>(activeController.getState());
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => activeController.subscribe(setState), [activeController]);

  useEffect(() => {
    let cancelled = false;
    const restorePromise = bootstrap
      ? bootstrap().then(() => {
          if (!cancelled) {
            setState({
              ...activeController.getState(),
              status: 'unauthenticated',
              restoreError: null,
            });
          }
        })
      : activeController.restore();

    restorePromise.catch(() => {
      if (!cancelled) {
        setState({
          ...activeController.getState(),
          status: 'unknown',
          restoreError: 'Connection problem. Check your network and try again.',
        });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [activeController, bootstrap, retryToken]);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      controller: activeController,
      retryRestore: () => setRetryToken(token => token + 1),
      clearSessionExpired: () => activeController.dismissSessionExpired(),
    }),
    [activeController, state],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error('useAuth must be used inside AuthProvider');
  }
  return value;
}
