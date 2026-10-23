import logging.config

from geotrack.config import LogLevel


def configure_logging(level: LogLevel) -> None:
    """One plain-text handler on stderr; uvicorn's access log stays off."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "plain": {
                    "format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                }
            },
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "formatter": "plain",
                    "stream": "ext://sys.stderr",
                }
            },
            "root": {"level": level, "handlers": ["stderr"]},
            "loggers": {
                "uvicorn": {"level": level},
                "uvicorn.error": {"level": level},
                "uvicorn.access": {"level": "WARNING", "propagate": False},
            },
        }
    )
