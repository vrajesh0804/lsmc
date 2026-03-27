from flask import Response
from typing import Dict, Tuple


def artifact_response_for(
    base_step: str,
    stable_cache: Dict[str, Tuple[int, bytes, Dict[str, str]]],
    artifact_ok_status: int,
) -> Response:
	# It returns a fake HTTP response for a step that is treated as an artifact.
	# create artificial failures for dropped, delayed
    parts = base_step.split(":", 3)
    method = parts[2] if len(parts) >= 3 else ""
    if method == "HEAD":
        return Response(b"", status=404)
    if base_step in stable_cache:
        status, body, hdrs = stable_cache[base_step]
        return Response(body, status=status, headers=hdrs)

    return Response(b"", status=artifact_ok_status)