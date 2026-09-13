"""Page tests for /capture."""

from fastapi.testclient import TestClient


def test_capture_prefill_carries_real_openers(authenticated_client: TestClient) -> None:
    """The quick actions link here with ?prefill=…; the textarea must not end up empty.

    The prefill lives in template-embedded JS, so this asserts on the served
    source: the openers are present and the old bracket placeholders are gone.
    """
    r = authenticated_client.get("/capture/?prefill=meal")
    assert r.status_code == 200, r.text[:500]
    for opener in ("'We had '", "'I did '", "'I bought '", "'My weight today is '"):
        assert opener in r.text, opener
    assert "[meal_type]" not in r.text
    assert "this.captureText = prefillMap[prefill]" in r.text
