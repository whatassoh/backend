"""Official LangGraph PostgresSaver lifecycle and configuration."""

from contextlib import contextmanager
from urllib.parse import quote_plus

from django.conf import settings


def postgres_checkpoint_uri():
    config = settings.DATABASES["default"]
    user = quote_plus(str(config["USER"]))
    password = quote_plus(str(config["PASSWORD"]))
    host = config.get("HOST") or "localhost"
    port = config.get("PORT") or 5432
    database = quote_plus(str(config["NAME"]))
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


@contextmanager
def postgres_saver():
    """Yield the official saver; callers never log its credential-bearing URI."""

    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(postgres_checkpoint_uri()) as saver:
        yield saver
