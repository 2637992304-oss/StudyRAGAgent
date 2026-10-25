from dotenv import dotenv_values

from app.core.config import ENV_FILE, Settings, get_settings


def test_dotenv_path_does_not_depend_on_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    values = dotenv_values(ENV_FILE)
    settings = Settings()
    if values.get("LLM_MODEL"):
        assert settings.llm_model == values["LLM_MODEL"]


def test_configuration_override_warning_does_not_print_key(monkeypatch, caplog):
    monkeypatch.setenv("LLM_MODEL", "overridden-test-model")
    monkeypatch.setenv("LLM_API_KEY", "private-test-key")
    settings = get_settings.__wrapped__()
    assert settings.llm_model == "overridden-test-model"
    if ENV_FILE.exists():
        assert "LLM_MODEL" in caplog.text
        assert "private-test-key" not in caplog.text
