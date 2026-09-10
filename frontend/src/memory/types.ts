export type MemoryType =
  | 'fact'
  | 'preference'
  | 'event'
  | 'relationship'
  | 'routine'
  | 'project'
  | 'summary';

export type MemoryItem = {
  id: string;
  content: string;
  metadata: Record<string, unknown> | null;
  memory_type: MemoryType;
  subject: string | null;
  predicate: string | null;
  object_json: Record<string, unknown> | null;
  confidence: number;
  salience: number;
  supersedes_id: string | null;
  status: 'active' | 'superseded' | 'deleted';
  created_at: string;
  updated_at: string;
};

export type MemorySettings = {
  enabled: boolean;
  timezone: string;
  locale: string;
  version: number;
};

export type VoiceSession = {
  id: string;
  client_metadata: Record<string, unknown> | null;
};

export type MemoryUpdate = {
  content: string;
};

export type MemoryCreate = {
  content: string;
  memory_type?: MemoryType;
};
