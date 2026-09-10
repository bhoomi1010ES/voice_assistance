import React from 'react';
import { View } from 'react-native';
import { useAppTheme } from '../design/ThemeProvider';

export function ThemeProbe() {
  const { mode, colors } = useAppTheme();
  return (
    <View
      testID="theme-probe"
      accessibilityLabel={mode}
      style={{ backgroundColor: colors.background }}
    />
  );
}
