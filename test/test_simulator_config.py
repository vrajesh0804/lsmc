from src.simcore.simulator_config import load_config


def test_load_config_defaults(monkeypatch):
    monkeypatch.delenv("SIM_404_AS_FAIL", raising=False)
    monkeypatch.delenv("SIM_408_AS_TIMEOUT", raising=False)
    monkeypatch.delenv("SIM_ENABLE_DROP", raising=False)
    monkeypatch.delenv("SIM_ENABLE_DELAY", raising=False)
    monkeypatch.delenv("SIM_DELAY_SECONDS", raising=False)
    monkeypatch.delenv("SIM_FORCED_PREFIX_TIMEOUT", raising=False)
    monkeypatch.delenv("SIM_PHASE_ORDER", raising=False)
    monkeypatch.delenv("SIM_ARTIFACT_OK_STATUS", raising=False)

    cfg = load_config()

    assert cfg.treat_404_as_fail is False
    assert cfg.treat_408_as_timeout is False
    assert cfg.enable_drop is False
    assert cfg.enable_delay is False


def test_load_config_reads_408_timeout_flag(monkeypatch):
    monkeypatch.setenv("SIM_408_AS_TIMEOUT", "1")

    cfg = load_config()

    assert cfg.treat_408_as_timeout is True


def test_load_config_reads_404_fail_flag(monkeypatch):
    monkeypatch.setenv("SIM_404_AS_FAIL", "1")

    cfg = load_config()

    assert cfg.treat_404_as_fail is True