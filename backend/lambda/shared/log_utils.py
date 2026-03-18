# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Structured CloudWatch logging with credential redaction.

Several handlers log their whole invocation event to make Step Functions
payloads traceable. Those payloads carry the short-lived SDMA write
credentials that prepare-render fetches, so logging them verbatim writes
usable secrets into CloudWatch. Redaction lives here, in the one function
every handler already logs through, rather than at each call site — a
call site that forgets to redact leaks silently.
"""
import json
from datetime import datetime

# Substrings that mark a value as secret. Matched case-insensitively
# against the key, so 'SecretAccessKey' and 'secret_access_key' both hit.
_SENSITIVE_KEY_PARTS = (
    'secretaccesskey',
    'sessiontoken',
    'accesskeyid',
    'password',
    'authorization',
)

_REDACTED = '[REDACTED]'
_TRUNCATED = '[TRUNCATED]'


def _redact(value, _depth: int = 0):
    """Return value with secret-looking entries replaced.

    Recurses through dicts and lists because the credentials sit several
    levels down inside a Step Functions payload. Past the depth cap the
    value is dropped rather than returned as-is: returning it would let a
    self-referential payload reach json.dumps, which raises on a circular
    reference and would lose the whole log line.
    """
    if _depth > 12:
        return _TRUNCATED

    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower().replace('_', '').replace('-', '')
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                redacted[key] = _REDACTED
            else:
                redacted[key] = _redact(item, _depth + 1)
        return redacted

    if isinstance(value, (list, tuple)):
        return [_redact(item, _depth + 1) for item in value]

    return value


def log_event(event_type: str, **kwargs):
    """Log a structured JSON message for CloudWatch, secrets redacted."""
    print(json.dumps({
        'event': event_type,
        'timestamp': datetime.now().isoformat(),
        **_redact(kwargs),
    }, default=str))
