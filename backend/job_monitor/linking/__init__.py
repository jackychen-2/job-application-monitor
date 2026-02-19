"""Thread-based email linking for job application lifecycle tracking."""

from job_monitor.linking.resolver import (
    resolve_by_thread_id,
    is_message_already_processed,
    ThreadLinkResult,
)

__all__ = [
    "resolve_by_thread_id",
    "is_message_already_processed",
    "ThreadLinkResult",
]
