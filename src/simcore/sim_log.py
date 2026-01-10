from flask import request
from src.simulator_logger import log_request, detect_service

def write_sim_log(step_parser, step: str, status: int, msg: str, run_no: int, step_index: int):
    """
    Best-effort logging to simulator_logger.py.
    This must NEVER break execution.
    """
    try:
        info = step_parser(step)
        headers_lower = {k.lower(): v for k, v in request.headers.items()}
        service = detect_service(headers_lower, info["method"], info["path"])
        body_text = request.get_data(as_text=True) or ""

        log_request(
            client_ip=request.remote_addr or "unknown",
            service=service,
            method=info["method"],
            path=info["path"],
            body=body_text,
            status=status,
            resp_headers={},
            client_message=msg,
            run_no=run_no,
            step_index=step_index,
        )
    except Exception:
        pass
