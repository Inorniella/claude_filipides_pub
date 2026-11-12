"""Configuration from environment variables (.env locally, secrets in GitHub Actions)."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def env(name: str, required: bool = False, default: str = "") -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Missing environment variable {name} (see .env.example)")
    return value
