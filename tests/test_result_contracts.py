"""Modern service and frozen legacy result-envelope contracts."""

import json

from jaws.domain import (
    DomainError,
    ErrorCategory,
    Failure,
    Success,
    legacy_failure,
    legacy_success,
    structured_envelope,
)
from jaws.jaws_utils import Reporter


def test_structured_success_and_failure_are_versioned():
    assert structured_envelope(Success({"count": 2})) == {
        "schema_version": "1.0.0",
        "ok": True,
        "data": {"count": 2},
    }
    error = DomainError(ErrorCategory.STORAGE, "graph.unavailable", "graph unavailable")
    assert structured_envelope(Failure(error)) == {
        "schema_version": "1.0.0",
        "ok": False,
        "error": {
            "category": "storage",
            "code": "graph.unavailable",
            "message": "graph unavailable",
            "details": {},
            "retryable": False,
        },
    }


def test_legacy_helpers_preserve_flat_shape_and_key_order():
    success = legacy_success({"count": 2})
    failure = legacy_failure("boom", exit_code=7)
    assert json.dumps(success) == '{"ok": true, "count": 2}'
    assert json.dumps(failure) == '{"ok": false, "error": "boom", "exit_code": 7}'


def test_reporter_preserves_exact_agent_json_bytes(capsys):
    reporter = Reporter(agent=True)
    reporter.result({"count": 2})
    assert capsys.readouterr().out == '{\n  "ok": true,\n  "count": 2\n}\n'
    reporter.error("ERROR", "boom")
    assert capsys.readouterr().out == '{"ok": false, "error": "boom"}\n'
