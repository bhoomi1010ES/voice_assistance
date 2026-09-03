from __future__ import annotations


class LLMError(RuntimeError):
    """Base safe error exposed by the provider-neutral LLM layer."""

    code = "llm_provider_error"
    retryable = False

    def __init__(
        self,
        message: str = "The configured language-model provider request failed.",
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds


class LLMConfigurationError(LLMError):
    code = "llm_configuration_error"


class LLMAuthenticationError(LLMError):
    code = "llm_authentication_error"


class LLMPermissionError(LLMError):
    code = "llm_permission_error"


class LLMModelNotFoundError(LLMError):
    code = "llm_model_not_found"


class LLMInvalidRequestError(LLMError):
    code = "llm_invalid_request"


class LLMRateLimitError(LLMError):
    code = "llm_rate_limited"
    retryable = True


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    retryable = True


class LLMOverloadedError(LLMError):
    code = "llm_overloaded"
    retryable = True


class LLMProviderError(LLMError):
    code = "llm_provider_error"
    retryable = True


class LLMProtocolError(LLMError):
    code = "llm_protocol_error"


class LLMCancelledError(LLMError):
    code = "llm_cancelled"


class LLMContextLimitError(LLMError):
    code = "llm_context_limit"


class LLMToolError(LLMError):
    code = "llm_tool_error"


class LLMToolAuthorizationError(LLMToolError):
    code = "llm_tool_not_authorized"


class LLMToolArgumentsError(LLMToolError):
    code = "llm_tool_invalid_arguments"


class LLMToolLoopLimitError(LLMToolError):
    code = "llm_tool_loop_limit"
