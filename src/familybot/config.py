from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str
    telegram_allowed_user_id: int

    timezone: str = "Europe/Berlin"
    morning_review_time: str = "07:15"
    evening_review_time: str = "20:30"
    urgent_reminder_times: str = "09:00,15:00,19:00"

    notion_token: str
    notion_data_source_id: str
    notion_version: str = "2026-03-11"

    # One OpenAI API key is used for both task parsing and voice transcription.
    openai_api_key: str
    openai_model: str = "gpt-5.6-luna"
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    transcription_language: str = "de"

    default_area: str = "Sonstiges"


settings = Settings()
