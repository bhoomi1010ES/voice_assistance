import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { useAuth } from '../auth/AuthProvider';
import {
  VoiceSocket,
  VoiceSocketOptions,
  VoiceSocketSnapshot,
} from './VoiceSocket';

export type VoiceSocketContextValue = VoiceSocketSnapshot & {
  socket: VoiceSocket;
};

const VoiceSocketContext = createContext<VoiceSocketContextValue | null>(null);

export function VoiceSocketProvider({
  children,
  socket,
  options,
}: {
  children: React.ReactNode;
  socket?: VoiceSocket;
  options?: VoiceSocketOptions;
}) {
  const { status } = useAuth();
  const activeSocket = useMemo(
    () => socket ?? new VoiceSocket(options),
    [options, socket],
  );
  const [snapshot, setSnapshot] = useState(activeSocket.getSnapshot());

  useEffect(() => activeSocket.subscribe(setSnapshot), [activeSocket]);

  useEffect(() => {
    if (status === 'authenticated') {
      activeSocket.start();
    } else if (status === 'unauthenticated') {
      activeSocket.stop('auth_state_changed').catch(() => undefined);
    }
  }, [activeSocket, status]);

  useEffect(
    () => () => {
      activeSocket.dispose().catch(() => undefined);
    },
    [activeSocket],
  );

  const value = useMemo<VoiceSocketContextValue>(
    () => ({ ...snapshot, socket: activeSocket }),
    [activeSocket, snapshot],
  );

  return (
    <VoiceSocketContext.Provider value={value}>
      {children}
    </VoiceSocketContext.Provider>
  );
}

export function useVoiceSocket(): VoiceSocketContextValue {
  const value = useContext(VoiceSocketContext);
  if (!value) {
    throw new Error('useVoiceSocket must be used inside VoiceSocketProvider');
  }
  return value;
}
