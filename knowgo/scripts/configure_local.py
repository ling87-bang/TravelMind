"""Configure the local demo without printing or committing credentials."""
import argparse
from pathlib import Path
import secrets

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "knowgo_travel_backend"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-model-from", type=Path)
    args = parser.parse_args()
    root_env = ROOT / ".env"
    root_env.touch(exist_ok=True)
    infra = dotenv_values(root_env)
    password = infra.get("POSTGRES_PASSWORD") or secrets.token_hex(16)
    set_key(root_env, "POSTGRES_PASSWORD", password)

    backend_env = BACKEND / ".env"
    backend_env.touch(exist_ok=True)
    existing = dotenv_values(backend_env)
    values = {
        "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": "15433",
        "POSTGRES_DB": "travel_planner_db", "POSTGRES_USER": "knowgo",
        "POSTGRES_PASSWORD": password,
        "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "16379",
        "APP_HOST": "127.0.0.1", "APP_PORT": "8000", "DEBUG": "false",
        "LANGSMITH_TRACING": "false", "DEEPEVAL_TELEMETRY_OPT_OUT": "YES",
    }
    if not existing.get("JWT_SECRET_KEY") or existing.get("JWT_SECRET_KEY") == "travel-jwt-secret-change-in-prod":
        values["JWT_SECRET_KEY"] = secrets.token_hex(32)
    if args.reuse_model_from:
        source = dotenv_values(args.reuse_model_from)
        for key in ("DASHSCOPE_API_KEY", "QWEN_MODEL_NAME", "QWEN_BASE_URL"):
            if not existing.get(key) and source.get(key):
                values[key] = source[key]
    for key, value in values.items():
        set_key(backend_env, key, value)
    configured = bool(dotenv_values(backend_env).get("DASHSCOPE_API_KEY"))
    print("Local database and API configuration saved.")
    print("Primary model: " + ("configured" if configured else "set DASHSCOPE_API_KEY in backend .env"))
    print("No credentials were printed. Existing optional service keys were preserved.")


if __name__ == "__main__":
    main()
