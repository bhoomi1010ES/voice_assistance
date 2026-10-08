import { AppIcon } from '../ui/AppIcon';
import React from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

interface MemorySearchBarProps {
  query: string;
  onQueryChange: (query: string) => void;
  onSearch: () => void;
  searching: boolean;
  enabled: boolean;
}

export function MemorySearchBar({
  query,
  onQueryChange,
  onSearch,
  searching,
  enabled,
}: MemorySearchBarProps) {
  const { colors } = useAppTheme();

  return (
    <View
      style={[
        styles.container,
        {
          backgroundColor: colors.surface,
          borderColor: colors.borderSubtle,
        },
        shadows.sm,
      ]}
      testID="memory-search-card"
    >
      <View style={styles.iconCircle}>
        <AppIcon name="Search" size={22} />
      </View>

      <TextInput
        accessibilityLabel={strings.memory.searchLabel}
        editable={enabled && !searching}
        onChangeText={onQueryChange}
        onSubmitEditing={onSearch}
        placeholder={strings.memory.searchPlaceholder}
        placeholderTextColor={colors.textSubtle}
        returnKeyType="search"
        style={[
          styles.input,
          {
            color: colors.text,
          },
        ]}
        testID="memory-search-input"
        value={query}
      />

      <Pressable
        accessibilityLabel={strings.memory.search}
        accessibilityRole="button"
        disabled={!enabled || searching}
        onPress={onSearch}
        style={({ pressed }) => [
          styles.searchButton,
          {
            opacity: !enabled || searching ? 0.5 : pressed ? 0.85 : 1,
          },
        ]}
        testID="memory-search"
      >
        <Text style={styles.searchButtonText}>
          {searching ? strings.memory.searching : strings.memory.search}
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    borderRadius: radii.xl,
    borderWidth: 1,
    flexDirection: 'row',
    gap: spacing.xs,
    padding: 6,
  },
  iconCircle: {
    alignItems: 'center',
    backgroundColor: '#E3EFFF',
    borderRadius: 20,
    height: 40,
    justifyContent: 'center',
    marginLeft: 2,
    width: 40,
  },
  icon: {
    fontSize: 18,
  },
  input: {
    flex: 1,
    fontSize: 15,
    paddingHorizontal: spacing.xs,
    paddingVertical: spacing.xs,
  },
  searchButton: {
    alignItems: 'center',
    backgroundColor: '#0969F5',
    borderRadius: 18,
    justifyContent: 'center',
    paddingHorizontal: 18,
    paddingVertical: 10,
  },
  searchButtonText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '700',
  },
});
