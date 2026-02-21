"""Email linking for job application lifecycle tracking."""

from job_monitor.linking.resolver import (
    is_message_already_processed,
    resolve_by_company,
    titles_similar,
    LinkResult,
    ThreadLinkResult,
)

__all__ = [
    "is_message_already_processed",
    "resolve_by_company",
    "titles_similar",
    "LinkResult",
    "ThreadLinkResult",
]
