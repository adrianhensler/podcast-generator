from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    openrouter_api_key: str = ""
    replicate_api_token: str = ""
    tavily_api_key: str = ""
    database_url: str = "sqlite:///./podcast_studio.db"
    output_dir: str = "output"
    replicate_concurrency: int = 5

    # LLM models
    model_outline: str = "qwen/qwen3.5-35b-a3b"
    model_expand: str = "z-ai/glm-5"

    # Pricing table (per 1M tokens, USD)
    model_pricing: dict = Field(default={
        "qwen/qwen3.5-35b-a3b": {"input": 0.14, "output": 1.30},
        "qwen/qwen3.5-397b-a17b": {"input": 0.14, "output": 2.34},
        "z-ai/glm-5": {"input": 0.72, "output": 2.30},
        "z-ai/glm-4.7": {"input": 0.38, "output": 1.98},
    })

    # TTS model options (cost_per_m_chars = USD per million characters)
    tts_models: dict = Field(default={
        "turbo": {
            "url": "https://api.replicate.com/v1/models/minimax/speech-02-turbo/predictions",
            "cost_per_m_chars": 30.0,
            "label": "Standard",
        },
        "hd": {
            "url": "https://api.replicate.com/v1/models/minimax/speech-02-hd/predictions",
            "cost_per_m_chars": 50.0,
            "label": "HD",
        },
    })

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
