import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { safeUserMessage, toClientError } from '../api/errors';
import {
  ActionButton,
  AppText,
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
import { KnowledgeModeCard } from '../components/memory/KnowledgeModeCard';
import { KnowledgeModeModal } from '../components/memory/KnowledgeModeModal';
import { MemorySearchBar } from '../components/memory/MemorySearchBar';
import { MemoryCreateCard } from '../components/memory/MemoryCreateCard';
import { SessionMemoryCard } from '../components/memory/SessionMemoryCard';
import { MemoryConfirmModal } from '../components/memory/MemoryConfirmModal';
import { MemoryEmptyState } from '../components/memory/MemoryEmptyState';

type Confirmation = 'delete' | 'delete-all' | null;
const LATEST_MEMORY_LIMIT = 5;

export function MemoryScreen() {
  const { controller, status } = useAuth();
  const { colors } = useAppTheme();
  const { sessionId } = useVoiceSocket();
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [showAllMemories, setShowAllMemories] = useState(false);
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
  const [showKnowledgeMode, setShowKnowledgeMode] = useState(false);
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
      setSettings(
        await updateMemorySettings(controller, { enabled: !settings.enabled }),
      );
      setMessage(
        settings.enabled ? strings.memory.disabled : strings.memory.enabled,
      );
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const changeKnowledgeMode = async (knowledge_mode: 'rag' | 'okf') => {
    if (!settings || settings.knowledge_mode === knowledge_mode) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      setSettings(await updateMemorySettings(controller, { knowledge_mode }));
      setMessage(strings.memory.knowledgeModeUpdated);
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

  const isSearch = query.trim().length > 0;
  const displayedMemories = useMemo(() => {
    if (isSearch) return memories;
    const latest = [...memories].sort(
      (a, b) =>
        Date.parse(b.created_at) - Date.parse(a.created_at) ||
        b.id.localeCompare(a.id),
    );
    return showAllMemories ? latest : latest.slice(0, LATEST_MEMORY_LIMIT);
  }, [isSearch, memories, showAllMemories]);

  return (
    <Screen testID="memory-screen">
      <ScrollView contentContainerStyle={styles.content}>
        {error ? <StatusBanner tone="error">{error}</StatusBanner> : null}
        {message ? <StatusBanner>{message}</StatusBanner> : null}

        {/* 1. Search Bar */}
        <MemorySearchBar
          enabled={Boolean(settings?.enabled)}
          onQueryChange={setQuery}
          onSearch={runSearch}
          query={query}
          searching={searching}
        />

        {/* 2. Memory Settings Card */}
        <MemorySettingsCard
          enabled={Boolean(settings?.enabled)}
          loading={!settings}
          onToggle={toggleMemory}
          saving={saving}
        />

        {/* 3. Knowledge Mode Card */}
        <KnowledgeModeCard
          knowledgeMode={settings?.knowledge_mode ?? 'rag'}
          onPress={() => setShowKnowledgeMode(true)}
        />

        {/* 4. Save a Memory Card */}
        <MemoryCreateCard
          enabled={Boolean(settings?.enabled)}
          newContent={newContent}
          onContentChange={setNewContent}
          onSave={saveNewMemory}
          saving={saving}
        />

        {/* 5. Exclude current chat Card */}
        {sessionId ? (
          <SessionMemoryCard
            excluding={excluding}
            onToggle={toggleSessionExclusion}
            sessionExcluded={sessionExcluded}
          />
        ) : null}

        {/* 6. Latest Memories Section */}
        <View style={styles.listHeader}>
          <View style={styles.listTitleContainer}>
            <Text style={[styles.sectionTitle, { color: colors.text }]}>
              {isSearch
                ? strings.memory.searchResults
                : showAllMemories
                ? strings.memory.allMemories
                : strings.memory.latestMemories}
            </Text>
            <View
              style={[
                styles.countBadge,
                { backgroundColor: colors.surfaceMuted },
              ]}
            >
              <Text style={[styles.countText, { color: colors.textMuted }]}>
                {memories.length}
              </Text>
            </View>
          </View>

          <View style={styles.listActions}>
            <ActionButton
              disabled={saving || memories.length === 0}
              label={strings.memory.deleteAll}
              onPress={() => setConfirmation('delete-all')}
              style={styles.listAction}
              testID="memory-delete-all"
              variant="quiet"
            />
            {!isSearch && memories.length > LATEST_MEMORY_LIMIT ? (
              <ActionButton
                accessibilityState={{ expanded: showAllMemories }}
                label={
                  showAllMemories
                    ? strings.memory.showLatest
                    : strings.memory.viewAll
                }
                onPress={() => setShowAllMemories(value => !value)}
                style={styles.listAction}
                testID="memory-view-all"
                variant="quiet"
              />
            ) : null}
          </View>
        </View>

        {visibleState === 'loading' ? (
          <AppText style={styles.loadingText}>{strings.memory.loading}</AppText>
        ) : null}

        {visibleState === 'empty' ? (
          <MemoryEmptyState testID="memory-empty" text={strings.memory.empty} />
        ) : null}

        {/* Memory Items Stack */}
        {memories.length > 0 ? (
          <View
            style={[
              styles.memoriesContainer,
              {
                backgroundColor: colors.surface,
                borderColor: colors.borderSubtle,
              },
            ]}
          >
            {displayedMemories.map(memory => (
              <MemoryCard
                key={memory.id}
                memory={memory}
                onView={selectMemory}
                selected={selected?.id === memory.id}
              />
            ))}
          </View>
        ) : null}

        {/* Selected Memory Detail & Editor Sheet */}
        {selected ? (
          <MemoryDetailCard
            draft={draft}
            editing={editing}
            onCancel={() => {
              setDraft(selected.content);
              setEditing(false);
            }}
            onClose={() => {
              setSelected(null);
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

        {/* Knowledge Mode Modal */}
        <KnowledgeModeModal
          knowledgeMode={settings?.knowledge_mode ?? 'rag'}
          okfAvailable={Boolean(settings?.okf_available)}
          onClose={() => setShowKnowledgeMode(false)}
          onKnowledgeModeChange={changeKnowledgeMode}
          saving={saving}
          visible={showKnowledgeMode}
        />
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: {
    gap: spacing.md,
    paddingBottom: spacing.xxl,
  },
  listHeader: {
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  listTitleContainer: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs + 2,
  },
  sectionTitle: {
    flexShrink: 1,
    fontSize: 16,
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
  listActions: {
    alignItems: 'center',
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    justifyContent: 'space-between',
  },
  listAction: {
    maxWidth: '100%',
    paddingHorizontal: spacing.sm,
  },
  memoriesContainer: {
    borderRadius: radii.xl,
    borderWidth: 1,
    overflow: 'hidden',
    padding: spacing.xs,
  },
  loadingText: {
    fontSize: typography.body,
    paddingVertical: spacing.md,
  },
});
