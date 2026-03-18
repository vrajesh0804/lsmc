from src.simcore.sim_log import write_sim_log


def test_write_sim_log_never_throws_without_request_context():
    step_parser = lambda s: {
        "client": "c",
        "thread": "t",
        "method": "GET",
        "path": "/",
    }
    write_sim_log(step_parser, "c:t:GET:/", 200, "OK", run_no=1, step_index=1)