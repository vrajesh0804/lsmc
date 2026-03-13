# src/simcore/simulator_cache.py
from flask import Response
from typing import Dict, Tuple


def artifact_response_for(
    base_step: str,
    stable_cache: Dict[str, Tuple[int, bytes, Dict[str, str]]],
    artifact_ok_status: int,
) -> Response:
    """
    STRICT DROP semantics:

    - If method == HEAD  → ALWAYS return 404
      (do NOT use stable cache)
    - Otherwise:
        * If stable cache has a real response → return it
        * Else → return artifact OK (default 200)
    """
    parts = base_step.split(":", 3)
    method = parts[2] if len(parts) >= 3 else ""

    # 🔴 STRICT FIX: dropped HEAD always behaves like "not exists"
    if method == "HEAD":
        return Response(b"", status=404)

    # For non-HEAD operations we still allow stable cache reuse
    if base_step in stable_cache:
        status, body, hdrs = stable_cache[base_step]
        return Response(body, status=status, headers=hdrs)

    return Response(b"", status=artifact_ok_status)


def cache_response(resp) -> Tuple[int, bytes, Dict[str, str]]:
    """
    Extract (status, body, headers) from a requests.Response-like object.
    """
    status = int(resp.status_code)
    body = resp.content
    hdrs = {k: v for k, v in resp.headers.items()}
    return status, body, hdrs