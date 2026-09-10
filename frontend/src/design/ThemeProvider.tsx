import React, { createContext, useContext, useMemo } from 'react';
import { useColorScheme } from 'react-native';
import { colors, ColorTokens, ThemeMode } from './tokens';

type ThemeContextValue = {
  mode: ThemeMode;
  colors: ColorTokens;
};

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

export type ThemeProviderProps = {
  children: React.ReactNode;
  forcedMode?: ThemeMode;
};

export function ThemeProvider({ children, forcedMode }: ThemeProviderProps) {
  const systemScheme = useColorScheme();
  const mode: ThemeMode =
    forcedMode ?? (systemScheme === 'dark' ? 'dark' : 'light');
  const value = useMemo(() => ({ mode, colors: colors[mode] }), [mode]);

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useAppTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useAppTheme must be used inside ThemeProvider');
  }

  return context;
}
