import { AuthController } from '../auth/AuthController';
import {
  MemoryItem,
  MemoryCreate,
  MemorySettings,
  MemoryUpdate,
  VoiceSession,
} from './types';

export function listMemories(
  controller: AuthController,
): Promise<MemoryItem[]> {
  return controller.request<MemoryItem[]>('/memories?limit=100');
}

export function searchMemories(
  controller: AuthController,
  query: string,
): Promise<MemoryItem[]> {
  return controller.request<MemoryItem[]>(
    `/memories/search?query=${encodeURIComponent(query)}&limit=32`,
  );
}

export function getMemory(
  controller: AuthController,
  memoryId: string,
): Promise<MemoryItem> {
  return controller.request<MemoryItem>(
    `/memories/${encodeURIComponent(memoryId)}`,
  );
}

export function updateMemory(
  controller: AuthController,
  memoryId: string,
  update: MemoryUpdate,
): Promise<MemoryItem> {
  return controller.request<MemoryItem>(
    `/memories/${encodeURIComponent(memoryId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(update),
    },
  );
}

export function createMemory(
  controller: AuthController,
  create: MemoryCreate,
): Promise<MemoryItem> {
  return controller.request<MemoryItem>('/memories', {
    method: 'POST',
    body: JSON.stringify({
      content: create.content,
      memory_type: create.memory_type ?? 'fact',
    }),
  });
}

export function deleteMemory(
  controller: AuthController,
  memoryId: string,
): Promise<null> {
  return controller.request<null>(`/memories/${encodeURIComponent(memoryId)}`, {
    method: 'DELETE',
  });
}

export function deleteAllMemories(controller: AuthController): Promise<null> {
  return controller.request<null>('/memories', {
    method: 'DELETE',
    body: JSON.stringify({ confirmation: 'DELETE_ALL_MEMORY' }),
  });
}

export function getMemorySettings(
  controller: AuthController,
): Promise<MemorySettings> {
  return controller.request<MemorySettings>('/memories/settings');
}

export function updateMemorySettings(
  controller: AuthController,
  enabled: boolean,
): Promise<MemorySettings> {
  return controller.request<MemorySettings>('/memories/settings', {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  });
}

export function setSessionMemoryExclusion(
  controller: AuthController,
  sessionId: string,
  excluded: boolean,
): Promise<void> {
  return controller
    .request(`/sessions/${encodeURIComponent(sessionId)}/memory-exclusion`, {
      method: 'PUT',
      body: JSON.stringify({ excluded }),
    })
    .then(() => undefined);
}

export function getVoiceSession(
  controller: AuthController,
  sessionId: string,
): Promise<VoiceSession> {
  return controller.request<VoiceSession>(
    `/sessions/${encodeURIComponent(sessionId)}`,
  );
}
