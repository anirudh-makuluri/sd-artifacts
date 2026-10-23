"""Identify unavailable optional Bedrock configuration at the LLM boundary."""

from botocore.exceptions import (
    ClientError,
    ConfigParseError,
    CredentialRetrievalError,
    InvalidConfigError,
    InvalidRegionError,
    NoCredentialsError,
    NoRegionError,
    PartialCredentialsError,
    ProfileNotFound,
    TokenRetrievalError,
)


class BedrockUnavailableError(RuntimeError):
    """Bedrock cannot be used with the current optional configuration."""


_CONFIGURATION_CODES = {
    "AccessDeniedException",
    "UnauthorizedException",
    "UnrecognizedClientException",
    "InvalidClientTokenId",
    "InvalidSignatureException",
    "SignatureDoesNotMatch",
    "ExpiredToken",
    "ExpiredTokenException",
    "ValidationException",
    "ResourceNotFoundException",
}


def is_bedrock_unavailable(exc: BaseException) -> bool:
    """Recognize setup/auth failures, including exceptions wrapped by LangChain."""
    pending = [exc]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(
            current,
            (
                BedrockUnavailableError,
                NoCredentialsError,
                PartialCredentialsError,
                NoRegionError,
                ProfileNotFound,
                CredentialRetrievalError,
                TokenRetrievalError,
                InvalidConfigError,
                ConfigParseError,
                InvalidRegionError,
            ),
        ):
            return True
        if isinstance(current, ClientError):
            if current.response.get("Error", {}).get("Code") in _CONFIGURATION_CODES:
                return True
        # Older LangChain releases wrap service errors in a ValueError string.
        if isinstance(current, ValueError):
            message = str(current)
            if message.startswith("Error raised by bedrock service:") and any(
                code in message for code in _CONFIGURATION_CODES
            ):
                return True
        for nested in (current.__cause__, current.__context__):
            if nested is not None:
                pending.append(nested)
    return False
