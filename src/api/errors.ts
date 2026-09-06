export type ClientErrorKind =
  | 'http'
  | 'websocket'
  | 'network'
  | 'configuration'
  | 'unknown';

export type ClientErrorOptions = {
  kind: ClientErrorKind;
  code?: string;
  status?: number;
  retryable?: boolean;
  cause?: unknown;
};

export class ClientError extends Error {
  readonly kind: ClientErrorKind;
  readonly code: string;
  readonly status?: number;
  readonly retryable: boolean;

  constructor(message: string, options: ClientErrorOptions) {
    super(message);
    this.name = 'ClientError';
    this.kind = options.kind;
    this.code = options.code ?? 'CLIENT_ERROR';
    this.status = options.status;
    this.retryable = options.retryable ?? false;
    this.cause = options.cause;
  }

  readonly cause?: unknown;
}

export function toClientError(
  error: unknown,
  fallbackKind: ClientErrorKind = 'unknown',
): ClientError {
  if (error instanceof ClientError) {
    return error;
  }

  if (error instanceof TypeError) {
    return new ClientError('A network request could not be completed.', {
      kind: 'network',
      code: 'NETWORK_UNAVAILABLE',
      retryable: true,
      cause: error,
    });
  }

  return new ClientError('The operation could not be completed.', {
    kind: fallbackKind,
    code: 'CLIENT_OPERATION_FAILED',
    retryable: true,
    cause: error,
  });
}

export function safeUserMessage(error: ClientError): string {
  if (error.kind === 'configuration') {
    return 'The app is not configured for this environment.';
  }

  if (error.status === 401 || error.status === 403) {
    return 'Your session is no longer available. Please sign in again.';
  }

  if (error.status === 429) {
    return 'Too many requests. Please wait a moment and try again.';
  }

  if (
    error.retryable ||
    error.kind === 'network' ||
    error.kind === 'websocket'
  ) {
    return 'Connection problem. Check your network and try again.';
  }

  return 'Something went wrong. Please try again.';
}
