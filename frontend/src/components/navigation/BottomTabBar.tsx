import React from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../design/tokens';
import { strings } from '../../i18n/strings';

export type PrimaryTabRoute = 'assistant' | 'memory' | 'tasks' | 'settings';

export type BottomTabBarProps = {
  activeRoute: string;
  onNavigate: (route: PrimaryTabRoute) => void;
};

type TabConfig = {
  route: PrimaryTabRoute;
  label: string;
  icon: string;
  testID: string;
};

export function BottomTabBar({ activeRoute, onNavigate }: BottomTabBarProps) {
  const { colors } = useAppTheme();
  const insets = useSafeAreaInsets();

  // Highlight 'settings' if user is inside secondary routes (account, sessions, diagnostics)
  const isSettingsActive =
    activeRoute === 'settings' ||
    activeRoute === 'account' ||
    activeRoute === 'sessions' ||
    activeRoute === 'diagnostics';

  const tabs: TabConfig[] = [
    {
      route: 'assistant',
      label: strings.main.assistant,
      icon: '🎙',
      testID: 'tab-assistant',
    },
    {
      route: 'memory',
      label: strings.main.memory,
      icon: '✦',
      testID: 'tab-memory',
    },
    {
      route: 'tasks',
      label: strings.main.tasks,
      icon: '✓',
      testID: 'tab-tasks',
    },
    {
      route: 'settings',
      label: strings.main.settings,
      icon: '⚙',
      testID: 'tab-settings',
    },
  ];

  return (
    <View
      accessibilityRole="tablist"
      style={[
        styles.container,
        {
          backgroundColor: colors.surface,
          borderTopColor: colors.borderSubtle,
          paddingBottom: Math.max(insets.bottom, spacing.xs),
        },
        shadows.md,
      ]}
      testID="bottom-tab-bar"
    >
      <View style={styles.tabBarInner}>
        {tabs.map(tab => {
          const isSelected =
            tab.route === 'settings'
              ? isSettingsActive
              : activeRoute === tab.route;

          return (
            <Pressable
              key={tab.route}
              accessibilityLabel={tab.label}
              accessibilityRole="tab"
              accessibilityState={{ selected: isSelected }}
              hitSlop={spacing.xs}
              onPress={() => onNavigate(tab.route)}
              style={({ pressed }) => [
                styles.tabItem,
                { opacity: pressed ? 0.72 : 1 },
              ]}
              testID={tab.testID}
            >
              <View
                style={[
                  styles.iconBadge,
                  isSelected && {
                    backgroundColor: colors.primaryContainer,
                  },
                ]}
              >
                <Text
                  style={[
                    styles.tabIcon,
                    {
                      color: isSelected ? colors.primaryDark : colors.textMuted,
                    },
                  ]}
                >
                  {tab.icon}
                </Text>
              </View>
              <Text
                numberOfLines={1}
                style={[
                  styles.tabLabel,
                  {
                    color: isSelected ? colors.primaryDark : colors.textMuted,
                    fontWeight: isSelected ? '700' : '500',
                  },
                ]}
              >
                {tab.label}
              </Text>
              {isSelected ? (
                <View
                  style={[
                    styles.activeIndicator,
                    { backgroundColor: colors.primary },
                  ]}
                />
              ) : (
                <View style={styles.inactiveSpacer} />
              )}
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    borderTopWidth: 1,
    elevation: 8,
  },
  tabBarInner: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-around',
    minHeight: 56,
    paddingHorizontal: spacing.sm,
    paddingTop: spacing.xs,
  },
  tabItem: {
    alignItems: 'center',
    flex: 1,
    justifyContent: 'center',
    minHeight: 48,
    minWidth: 48,
    paddingVertical: spacing.xxs,
  },
  iconBadge: {
    alignItems: 'center',
    borderRadius: radii.full,
    height: 32,
    justifyContent: 'center',
    width: 48,
  },
  tabIcon: {
    fontSize: 18,
    lineHeight: 22,
  },
  tabLabel: {
    fontSize: typography.caption,
    lineHeight: 16,
    marginTop: 2,
    textAlign: 'center',
  },
  activeIndicator: {
    borderRadius: radii.full,
    height: 3,
    marginTop: 2,
    width: 16,
  },
  inactiveSpacer: {
    height: 3,
    marginTop: 2,
    width: 16,
  },
});
