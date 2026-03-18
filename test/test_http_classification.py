from main import _classify_client_error_from_lines
from src.simcore.sim_helpers import classify_http_failure


def test_classify_http_failure_404_is_success_when_flag_off():
    result = classify_http_failure(
        status=404,
        method="HEAD",
        full_path="/bucket-a",
        treat_404_as_fail=False,
        treat_408_as_timeout=False,
    )
    assert result is None


def test_classify_http_failure_404_is_failure_when_flag_on():
    result = classify_http_failure(
        status=404,
        method="HEAD",
        full_path="/bucket-a",
        treat_404_as_fail=True,
        treat_408_as_timeout=False,
    )
    assert result == ("FAILURE", "HEAD /bucket-a → 404")


def test_classify_http_failure_408_is_success_when_flag_off():
    result = classify_http_failure(
        status=408,
        method="PUT",
        full_path="/bucket-a",
        treat_404_as_fail=False,
        treat_408_as_timeout=False,
    )
    assert result is None


def test_classify_http_failure_408_is_timeout_when_flag_on():
    result = classify_http_failure(
        status=408,
        method="PUT",
        full_path="/bucket-a",
        treat_404_as_fail=False,
        treat_408_as_timeout=True,
    )
    assert result == ("TIMEOUT", "PUT /bucket-a → 408")


def test_classify_http_failure_500_is_failure():
    result = classify_http_failure(
        status=500,
        method="GET",
        full_path="/bucket-a",
        treat_404_as_fail=False,
        treat_408_as_timeout=False,
    )
    assert result == ("FAILURE", "GET /bucket-a → 500")


def test_client_error_parser_no_error():
    lines = [
        "Thread-1: create_bucket bucket-a",
        "CLIENT_HAD_ERROR=0",
    ]
    had_error, kind, detail = _classify_client_error_from_lines(
        lines,
        treat_408_as_timeout=False,
    )
    assert had_error is False
    assert kind is None
    assert detail == ""


def test_client_error_parser_408_is_success_when_flag_off():
    lines = [
        "Thread-1: FAIL ClientError 408",
        "CLIENT_HAD_ERROR=1",
    ]
    had_error, kind, detail = _classify_client_error_from_lines(
        lines,
        treat_408_as_timeout=False,
    )
    assert had_error is False
    assert kind is None
    assert detail == ""


def test_client_error_parser_408_is_timeout_when_flag_on():
    lines = [
        "Thread-1: FAIL ClientError 408",
        "CLIENT_HAD_ERROR=1",
    ]
    had_error, kind, detail = _classify_client_error_from_lines(
        lines,
        treat_408_as_timeout=True,
    )
    assert had_error is True
    assert kind == "TIMEOUT"
    assert "408" in detail


def test_client_error_parser_non_timeout_failure():
    lines = [
        "Thread-1: FAIL Exception (ClientError): An error occurred (500)",
        "CLIENT_HAD_ERROR=1",
    ]
    had_error, kind, detail = _classify_client_error_from_lines(
        lines,
        treat_408_as_timeout=False,
    )
    assert had_error is True
    assert kind == "FAILURE"
    assert "500" in detail


def test_client_error_parser_readtimeout_is_timeout():
    lines = [
        "Thread-1: FAIL ReadTimeoutError while waiting",
        "CLIENT_HAD_ERROR=1",
    ]
    had_error, kind, detail = _classify_client_error_from_lines(
        lines,
        treat_408_as_timeout=False,
    )
    assert had_error is True
    assert kind == "TIMEOUT"
    assert "ReadTimeout" in detail