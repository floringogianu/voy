"""Package constants and settings.
"""
import logging
import logging.config

import colorful as cf
from platformdirs import user_data_path, user_log_path

try:
    from .version import __version__
except ImportError:
    logging.exception("Probably you didn't run `pip install .`")
    raise

# set globals
VOY_PATH = user_data_path("voy", version=__version__, ensure_exists=True)
VOY_LOGS = user_log_path("voy", ensure_exists=True) / "voy.log"
CATEGORIES = ["cs.CV", "cs.LG", "cs.CL", "cs.AI", "cs.NE", "cs.RO"]


# set logging
logging.config.dictConfig(
    config={
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {"format": "%(levelname)s: %(message)s"},
            "detailed": {
                "format": "[%(asctime)s %(levelname)s %(module)s:L%(lineno)d] %(message)s",
                "datefmt": "%Y-%m-%dT%H:%M:%S%z",
            },
        },
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
                "level": "CRITICAL",
                "stream": "ext://sys.stderr",
            },
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
                "level": "WARNING",
                "stream": "ext://sys.stdout",
            },  # configured but unused
            "file": {
                "backupCount": 5,
                "class": "logging.handlers.RotatingFileHandler",
                "filename": VOY_LOGS,
                "formatter": "detailed",
                "level": "DEBUG",
                "maxBytes": 100_000,
            },
        },
        "loggers": {"root": {"level": "DEBUG", "handlers": ["stderr", "file"]}},
    }
)

# get application logger
log = logging.getLogger("voy")


# set colors
cf.use_8_ansi_colors()


log.info("set up complete.")
