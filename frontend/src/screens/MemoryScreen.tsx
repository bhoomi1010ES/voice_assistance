import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { safeUserMessage, toClientError } from '../api/errors';
import {
  ActionButton,
  AppText,
  Heading,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';
import { radii, spacing, typography } from '../theme';
import { strings } from '../i18n/strings';
import { useAuth } from '../auth/AuthProvider';
import { useVoiceSocket } from '../voice/VoiceSocketProvider';
import {
  deleteAllMemories,
  deleteMemory,
  createMemory,
  getMemorySettings,
  getVoiceSession,
  listMemories,
  searchMemories,
  setSessionMemoryExclusion,
  updateMemory,
  updateMemorySettings,
} from '../memory/api';
import { MemoryItem, MemorySettings } from '../memory/types';

import { MemoryCard } from '../components/memory/MemoryCard';
import { MemoryDetailCard } from '../components/memory/MemoryDetailCard';
import { MemorySettingsCard } from '../components/memory/MemorySettingsCard';
import { MemorySearchBar } from '../components/memory/MemorySearchBar';
import { MemoryCreateCard } from '../components/memory/MemoryCreateCard';
import { SessionMemoryCard } from '../components/memory/SessionMemoryCard';
import { MemoryConfirmModal } from '../components/memory/MemoryConfirmModal';
import { MemoryEmptyState } from '../components/memory/MemoryEmptyState';

type Confirmation = 'delete' | 'delete-all' | null;

export function MemoryScreen() {
  const { controller, status } = useAuth();
  const { colors } = useAppTheme();
  const { sessionId } = useVoiceSocket();
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [selected, setSelected] = useState<MemoryItem | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [newContent, setNewContent] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [excluding, setExcluding] = useState(false);
  const [sessionExcluded, setSessionExcluded] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const searchSequence = useRef(0);
  const hasSearched = useRef(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [settingsResult, memoriesResult] = await Promise.allSettled([
      getMemorySettings(controller),
      listMemories(controller),
    ]);
    if (settingsResult.status === 'fulfilled') {
      setSettings(settingsResult.value);
    }
    if (memoriesResult.status === 'fulfilled') {
      setMemories(memoriesResult.value);
    }
    const failure = [settingsResult, memoriesResult].find(
      result => result.status === 'rejected',
    );
    if (failure?.status === 'rejected') {
      setError(safeUserMessage(toClientError(failure.reason)));
    }
    setLoading(false);
  }, [controller]);

  useEffect(() => {
    if (status !== 'authenticated') {
      return;
    }
    load().catch(cause => setError(safeUserMessage(toClientError(cause))));
  }, [load, status]);

  useEffect(() => {
    if (status !== 'authenticated' || !sessionId) {
      setSessionExcluded(false);
      return;
    }
    getVoiceSession(controller, sessionId)
      .then(session => {
        setSessionExcluded(session.client_metadata?.memory_excluded === true);
      })
      .catch(cause => setError(safeUserMessage(toClientError(cause))));
  }, [controller, sessionId, status]);

  const search = useCallback(
    async (normalizedQuery: string, sequence: number) => {
      hasSearched.current = true;
      setSearching(true);
      setError(null);
      try {
        const result = await searchMemories(controller, normalizedQuery);
        if (sequence === searchSequence.current) {
          setMemories(result);
        }
      } catch (cause) {
        if (sequence === searchSequence.current) {
          setError(safeUserMessage(toClientError(cause)));
        }
      } finally {
        if (sequence === searchSequence.current) {
          setSearching(false);
        }
      }
    },
    [controller],
  );

  const runSearch = useCallback(() => {
    const normalizedQuery = query.trim();
    if (searchTimer.current) {
      clearTimeout(searchTimer.current);
      searchTimer.current = null;
    }
    const sequence = ++searchSequence.current;
    if (!normalizedQuery) {
      hasSearched.current = false;
      setSearching(false);
      load().catch(cause => setError(safeUserMessage(toClientError(cause))));
      return;
    }
    search(normalizedQuery, sequence).catch(() => undefined);
  }, [load, query, search]);

  useEffect(() => {
    if (!settings?.enabled || !query.trim()) {
      setSearching(false);
      if (hasSearched.current && !query.trim()) {
        const sequence = ++searchSequence.current;
        hasSearched.current = false;
        load().catch(cause => {
          if (sequence === searchSequence.current) {
            setError(safeUserMessage(toClientError(cause)));
          }
        });
      }
      return;
    }
    const sequence = ++searchSequence.current;
    searchTimer.current = setTimeout(() => {
      searchTimer.current = null;
      search(query.trim(), sequence).catch(() => undefined);
    }, 300);
    return () => {
      if (searchTimer.current) {
        clearTimeout(searchTimer.current);
        searchTimer.current = null;
      }
    };
  }, [load, query, search, settings?.enabled]);

  const selectMemory = (memory: MemoryItem) => {
    setSelected(memory);
    setDraft(memory.content);
    setEditing(false);
    setMessage(null);
    setError(null);
  };

  const save = async () => {
    if (!selected || !draft.trim()) {
      setError(strings.memory.invalidContent);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const replacement = await updateMemory(controller, selected.id, {
        content: draft.trim(),
      });
      setMemories(items =>
        items.map(item => (item.id === selected.id ? replacement : item)),
      );
      setSelected(replacement);
      setDraft(replacement.content);
      setEditing(false);
      setMessage(strings.memory.updated);
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const removeSelected = async () => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      await deleteMemory(controller, selected.id);
      setMemories(items => items.filter(item => item.id !== selected.id));
      setSelected(null);
      setConfirmation(null);
      setMessage(strings.memory.deleted);
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const removeAll = async () => {
    setSaving(true);
    setError(null);
    try {
      await deleteAllMemories(controller);
      setMemories([]);
      setSelected(null);
      setConfirmation(null);
      setMessage(strings.memory.deletedAll);
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const toggleMemory = async () => {
    if (!settings) return;
    setSaving(true);
    setError(null);
    try {
      setSettings(await updateMemorySettings(controller, !settings.enabled));
      setMessage(
        settings.enabled ? strings.memory.disabled : strings.memory.enabled,
      );
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const saveNewMemory = async () => {
    const content = newContent.trim();
    if (!content) {
      setError(strings.memory.invalidContent);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await createMemory(controller, { content });
      setMemories(items => [
        created,
        ...items.filter(item => item.id !== created.id),
      ]);
      setNewContent('');
      setSelected(created);
      setMessage(strings.memory.created);
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const toggleSessionExclusion = async () => {
    if (!sessionId) return;
    setExcluding(true);
    setError(null);
    try {
      const nextValue = !sessionExcluded;
      await setSessionMemoryExclusion(controller, sessionId, nextValue);
      setSessionExcluded(nextValue);
      setMessage(
        nextValue
          ? strings.memory.sessionExcluded
          : strings.memory.sessionIncluded,
      );
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setExcluding(false);
    }
  };

  const visibleState = useMemo(() => {
    if (loading) return 'loading';
    if (memories.length === 0) return 'empty';
    return 'success';
  }, [loading, memories.length]);

  return (
    <Screen testID="memory-screen">
      <ScrollView contentContainerStyle={styles.content}>
        {/* Stitch-inspired Hero Header */}
        <View style={styles.header}>
          <AppText style={[styles.overline, { color: colors.primary }]}>
            KNOWLEDGE & CONTEXT
          </AppText>
          <Heading>{strings.memory.title}</Heading>
          <AppText style={[styles.body, { color: colors.textMuted }]}>
            {strings.memory.body}
          </AppText>
        </View>

        {error ? <StatusBanner tone="error">{error}</StatusBanner> : null}
        {message ? <StatusBanner>{message}</StatusBanner> : null}

        {/* Master Memory Settings Card */}
        <MemorySettingsCard
          enabled={Boolean(settings?.enabled)}
          loading={!settings}
          onToggle={toggleMemory}
          saving={saving}
        />

        {/* Debounced Search Bar */}
        <MemorySearchBar
          enabled={Boolean(settings?.enabled)}
          onQueryChange={setQuery}
          onSearch={runSearch}
          query={query}
          searching={searching}
        />

        {/* Add Memory Card */}
        <MemoryCreateCard
          enabled={Boolean(settings?.enabled)}
          newContent={newContent}
          onContentChange={setNewContent}
          onSave={saveNewMemory}
          saving={saving}
        />

        {/* Active Session Exclusion Card (Conditional on active session) */}
        {sessionId ? (
          <SessionMemoryCard
            excluding={excluding}
            onToggle={toggleSessionExclusion}
            sessionExcluded={sessionExcluded}
          />
        ) : null}

        {/* Memory List Header */}
        <View style={styles.listHeader}>
          <View style={styles.listTitleContainer}>
            <AppText style={[styles.sectionTitle, { color: colors.text }]}>
              {strings.memory.listTitle}
            </AppText>
            <View
              style={[
                styles.countBadge,
                { backgroundColor: colors.surfaceMuted },
              ]}
            >
              <AppText style={[styles.countText, { color: colors.textMuted }]}>
                {memories.length}
              </AppText>
            </View>
          </View>

          <ActionButton
            disabled={saving || memories.length === 0}
            label={strings.memory.deleteAll}
            onPress={() => setConfirmation('delete-all')}
            testID="memory-delete-all"
            variant="quiet"
          />
        </View>

        {visibleState === 'loading' ? (
          <AppText style={styles.loadingText}>{strings.memory.loading}</AppText>
        ) : null}

        {visibleState === 'empty' ? (
          <MemoryEmptyState
            testID="memory-empty"
            text={strings.memory.empty}
          />
        ) : null}

        {/* Memory Items Stack */}
        {memories.map(memory => (
          <MemoryCard
            key={memory.id}
            memory={memory}
            onView={selectMemory}
            selected={selected?.id === memory.id}
          />
        ))}

        {/* Selected Memory Detail & Editor */}
        {selected ? (
          <MemoryDetailCard
            draft={draft}
            editing={editing}
            onCancel={() => {
              setDraft(selected.content);
              setEditing(false);
            }}
            onDelete={() => setConfirmation('delete')}
            onDraftChange={setDraft}
            onEdit={() => setEditing(true)}
            onSave={save}
            saving={saving}
            selected={selected}
          />
        ) : null}

        {/* Confirmation Modal */}
        {confirmation ? (
          <MemoryConfirmModal
            confirmation={confirmation}
            onCancel={() => setConfirmation(null)}
            onConfirm={confirmation === 'delete' ? removeSelected : removeAll}
            saving={saving}
          />
        ) : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: {
    gap: spacing.md,
    paddingBottom: spacing.xxl,
  },
  header: {
    gap: 2,
    marginBottom: spacing.xs,
  },
  overline: {
    fontSize: typography.caption,
    fontWeight: '700',
    letterSpacing: 1,
  },
  body: {
    fontSize: typography.caption,
    lineHeight: 18,
    marginTop: 2,
  },
  listHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: spacing.md,
  },
  listTitleContainer: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
  },
  sectionTitle: {
    fontSize: typography.subheading,
    fontWeight: '700',
  },
  countBadge: {
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  countText: {
    fontSize: typography.caption,
    fontWeight: '700',
  },
  loadingText: {
    fontSize: typography.body,
    paddingVertical: spacing.md,
  },
});
