/**
 * Playback-only barge-in policy.
 *
 * Silero continues to monitor the processed microphone stream while TTS is
 * active. On loudspeaker routes, the first residual echo can look like a
 * valid 160 ms speech transition. A short, evidence-based guard after actual
 * playback starts rejects that startup echo while leaving normal VAD and
 * deliberate interruptions after the guard unchanged.
 */
export const NORMAL_SPEECH_PROBABILITY_THRESHOLD = 0.5;
export const PLAYBACK_SPEECH_PROBABILITY_THRESHOLD = 0.75;
export const PLAYBACK_START_GUARD_MS = 1_200;

export type PlaybackBargeInCandidate = {
  playbackActive: boolean;
  playbackStartedAtMs: number | null;
  candidateAtMs: number;
  probability?: number | null;
};

export type PlaybackBargeInDecision = {
  accepted: boolean;
  reason: 'normal_vad' | 'near_end_speech' | 'likely_playback_echo';
  playbackAgeMs: number | null;
};

export function evaluatePlaybackBargeIn(
  candidate: PlaybackBargeInCandidate,
): PlaybackBargeInDecision {
  if (!candidate.playbackActive) {
    return {
      accepted: true,
      reason: 'normal_vad',
      playbackAgeMs: null,
    };
  }

  const playbackAgeMs =
    candidate.playbackStartedAtMs === null
      ? null
      : Math.max(0, candidate.candidateAtMs - candidate.playbackStartedAtMs);

  // A candidate before the guard expires is the known loudspeaker startup
  // echo pattern from the 120/160 and 160/160 physical runs.
  if (playbackAgeMs === null || playbackAgeMs < PLAYBACK_START_GUARD_MS) {
    return {
      accepted: false,
      reason: 'likely_playback_echo',
      playbackAgeMs,
    };
  }

  if (
    typeof candidate.probability === 'number' &&
    Number.isFinite(candidate.probability) &&
    candidate.probability < PLAYBACK_SPEECH_PROBABILITY_THRESHOLD
  ) {
    return {
      accepted: false,
      reason: 'likely_playback_echo',
      playbackAgeMs,
    };
  }

  return {
    accepted: true,
    reason: 'near_end_speech',
    playbackAgeMs,
  };
}
