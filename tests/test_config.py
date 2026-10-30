"""Configuración desde el archivo .env y el entorno."""

from ai_assistant.infrastructure import config


def test_env_file_values_with_interpolation(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=s3creta\n"
        "# comentario\n"
        "AI_ASSISTANT_PROVIDER=chatgpt_plan\n"
        "AI_ASSISTANT_DATABASE_URL=postgresql://u:${POSTGRES_PASSWORD}@127.0.0.1:${AI_ASSISTANT_DB_PORT:-5432}/db\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AI_ASSISTANT_DB_PORT", raising=False)
    monkeypatch.delenv("AI_ASSISTANT_PROVIDER", raising=False)
    monkeypatch.delenv("AI_ASSISTANT_DATABASE_URL", raising=False)
    monkeypatch.setattr(config, "ENV_FILE", env_file)

    settings = config.load_settings()
    assert settings.chat_provider == "chatgpt_plan"
    assert settings.database_url == "postgresql://u:s3creta@127.0.0.1:5432/db"


def test_process_environment_overrides_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("AI_ASSISTANT_PROVIDER=chatgpt_plan\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setenv("AI_ASSISTANT_PROVIDER", "fake")
    assert config.load_settings().chat_provider == "fake"


def test_missing_env_file_uses_defaults(monkeypatch):
    monkeypatch.delenv("AI_ASSISTANT_PROVIDER", raising=False)
    monkeypatch.delenv("AI_ASSISTANT_DATABASE_URL", raising=False)
    settings = config.load_settings()  # ENV_FILE apunta a un archivo inexistente (conftest).
    assert (settings.chat_provider, settings.database_url) == ("fake", None)
