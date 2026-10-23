import uvicorn

from geotrack.app import create_app
from geotrack.config import Settings
from geotrack.log_setup import configure_logging

# Browsers only send small viewport commands.
WS_MAX_MESSAGE_BYTES = 16 * 1024


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        server_header=False,
        ws_max_size=WS_MAX_MESSAGE_BYTES,
        timeout_graceful_shutdown=settings.shutdown_grace_seconds,
    )


if __name__ == "__main__":
    main()
