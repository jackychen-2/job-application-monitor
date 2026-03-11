import base64

from job_monitor.email.gmail_client import _build_message_from_full_payload


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8").rstrip("=")


def test_build_message_from_full_payload_prefers_plain_text() -> None:
    msg = _build_message_from_full_payload(
        {
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "Subject", "value": "AWS Application Response"},
                    {"name": "From", "value": "Amazon <jobs@amazon.com>"},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64("Plain body")},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"data": _b64("<p>HTML body</p>")},
                    },
                ],
            }
        },
        "gmail-1",
    )

    assert msg is not None
    assert msg["Subject"] == "AWS Application Response"
    assert msg["From"] == "Amazon <jobs@amazon.com>"
    assert "Plain body" in msg.get_payload()


def test_build_message_from_full_payload_uses_html_when_plain_missing() -> None:
    msg = _build_message_from_full_payload(
        {
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "Subject", "value": "Recruiter Reach-out"},
                ],
                "parts": [
                    {
                        "mimeType": "text/html",
                        "body": {"data": _b64("<p>Hello from AWS</p>")},
                    },
                ],
            }
        },
        "gmail-2",
    )

    assert msg is not None
    assert msg["Subject"] == "Recruiter Reach-out"
    assert "Hello from AWS" in msg.get_payload()
