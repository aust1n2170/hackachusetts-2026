from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ems:ems@localhost:5432/ems"
    rules_path: str = "rules/region-rules.yaml"
    # Leave empty to use the straight-line fallback. Example: http://osrm:5000
    osrm_url: str = ""
    osrm_timeout_seconds: float = 1.5
    search_radius_km: float = 60.0
    cors_origins: str = "http://localhost:5173"

    # Feed jobs
    simulate_feeds: bool = True
    simulate_stale_ids: str = "seu"   # hospitals whose simulated feed is left to go stale
    feed_interval_seconds: int = 60

    # Before-vs-after replay scenarios, and demo mode (scripted state + frozen feed).
    # Demo endpoints can wipe dispatches; the API has no auth, so keep this off anywhere public.
    scenarios_dir: str = "data/scenarios"
    demo_mode: bool = False


settings = Settings()
