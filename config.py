import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # API Keys
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "your-openai-key")
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "your-elevenlabs-key")
    runway_api_key: str = os.getenv("RUNWAY_API_KEY", "your-runway-key")
    
    # Infrastructure
    database_url: str = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/db")
    database_pool_size: int = 20
    celery_broker_url: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    celery_result_backend: str = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
    celery_task_time_limit: int = 7200
    
    # Defaults
    tts_voice_id: str = "en-US-Neural2-C"

def get_settings():
    return Settings()
