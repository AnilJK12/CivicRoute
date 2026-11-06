"""Gemini configuration tests use temporary dotenv files and fake credentials only."""

from backend.config import Settings, load_project_environment


def test_loads_project_dotenv_file_without_exposing_values(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "GEMINI_API_KEY=synthetic-test-credential\nGEMINI_MODEL=fixture-model\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    load_project_environment(tmp_path)
    settings = Settings.from_environment()

    assert settings.gemini_api_key is not None
    assert settings.gemini_model == "fixture-model"


def test_environment_variables_take_precedence_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "GEMINI_API_KEY=synthetic-file-credential\nGEMINI_MODEL=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-process-credential")
    monkeypatch.setenv("GEMINI_MODEL", "process-model")

    load_project_environment(tmp_path)
    settings = Settings.from_environment()

    assert settings.gemini_api_key == "synthetic-process-credential"
    assert settings.gemini_model == "process-model"


def test_missing_gemini_configuration_is_optional(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "  ")
    monkeypatch.setenv("GEMINI_MODEL", "  ")

    settings = Settings.from_environment()

    assert settings.gemini_api_key is None
    assert settings.gemini_model is None


def test_official_search_configuration_is_environment_backed(monkeypatch):
    monkeypatch.setenv("OFFICIAL_SOURCE_PROVIDER", "BRAVE")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "synthetic-search-credential")
    monkeypatch.setenv("OFFICIAL_SOURCE_APPROVED_HOSTS", "karnataka.gov.in, uidai.gov.in")

    settings = Settings.from_environment()

    assert settings.official_source_provider == "brave"
    assert settings.brave_search_api_key is not None
    assert settings.official_source_approved_hosts == ("karnataka.gov.in", "uidai.gov.in")
