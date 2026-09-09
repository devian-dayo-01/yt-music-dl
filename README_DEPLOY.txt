Railway auto-deploy configuration

This package is configured for Railway GitHub auto-deploy.

Railway reads railway.toml and starts:
uvicorn main:app --host 0.0.0.0 --port $PORT

Health check:
/health

Required dependency:
jinja2

Do not set a fixed application port. Railway supplies PORT at runtime.
