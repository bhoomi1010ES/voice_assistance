/**
 * Playback-only barge-in policy.
 *
 * Silero continues to monitor the processed microphone stream while TTS is
 * active. On loudspeaker routes, the first residual echo can look like a
 * valid 160 ms speech transition. A short, evidence-based guard after actual
 * playback starts rejects that startup echo. The caller additionally
 * requires sustained activity after the confirmed speech start before
 * cancelling an active response.
 */
export const NORMAL_SPEECH_PROBABILITY_THRESHOLD = 0.5;
export const PLAYBACK_SPEECH_PROBABILITY_THRESHOLD = 0.9;
export const PLAYBACK_START_GUARD_MS = 1_200;
export const PLAYBACK_BARGE_IN_CONFIRMATION_MS = 480;
export const PLAYBACK_ECHO_SIMILARITY_THRESHOLD = 0.58;

export type PlaybackBargeInCandidate = {
  playbackActive: boolean;
  playbackStartedAtMs: number | null;
  candidateAtMs: number;
  probability?: number | null;
  echoLikely?: boolean | null;
  echoSimilarity?: number | null;
  playbackReferenceAvailable?: boolean | null;
  playbackState?: string | null;
};

export type PlaybackBargeInDecision = {
  accepted: boolean;
  reason: 'normal_vad' | 'near_end_speech' | 'likely_playback_echo';
  playbackAgeMs: number | null;
  echoSimilarity: number | null;
};

export function evaluatePlaybackBargeIn(
  candidate: PlaybackBargeInCandidate,
): PlaybackBargeInDecision {
  if (!candidate.playbackActive) {
    return {
      accepted: true,
      reason: 'normal_vad',
      playbackAgeMs: null,
      echoSimilarity: null,
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
      echoSimilarity: normalizedEchoSimilarity(candidate.echoSimilarity),
    };
  }

  const echoSimilarity = normalizedEchoSimilarity(candidate.echoSimilarity);
  if (candidate.playbackReferenceAvailable === false) {
    return {
      accepted: false,
      reason: 'likely_playback_echo',
      playbackAgeMs,
      echoSimilarity,
    };
  }
  if (
    candidate.playbackState === 'TTS_TAIL_SUPPRESSION' ||
    candidate.echoLikely === true ||
    (echoSimilarity !== null &&
      echoSimilarity >= PLAYBACK_ECHO_SIMILARITY_THRESHOLD)
  ) {
    return {
      accepted: false,
      reason: 'likely_playback_echo',
      playbackAgeMs,
      echoSimilarity,
    };
  }

  // A playback-time event without a valid Silero probability is not enough
  // evidence to interrupt the assistant. Treat it as echo/noise instead of
  // allowing an unscored native event to cancel the response.
  if (
    typeof candidate.probability !== 'number' ||
    !Number.isFinite(candidate.probability) ||
    candidate.probability < PLAYBACK_SPEECH_PROBABILITY_THRESHOLD
  ) {
    return {
      accepted: false,
      reason: 'likely_playback_echo',
      playbackAgeMs,
      echoSimilarity,
    };
  }

  return {
    accepted: true,
    reason: 'near_end_speech',
    playbackAgeMs,
    echoSimilarity,
  };
}

function normalizedEchoSimilarity(
  value: number | null | undefined,
): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
