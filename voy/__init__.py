"""Package constants and settings.
"""
import colorful as cf
from platformdirs import user_data_path

try:
    from .version import __version__
except ImportError:
    print("Probably you didn't run `pip install .`")
    raise

# set globals
VOY_PATH = user_data_path("voy", version=__version__, ensure_exists=True)
CATEGORIES = ["cs.CV", "cs.LG", "cs.CL", "cs.AI", "cs.NE", "cs.RO"]

# set colors
cf.use_8_ansi_colors()
