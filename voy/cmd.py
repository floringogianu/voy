""" Here we define wrapper functions to be called when one invokes
    console commands.
"""

# pylint: disable=import-outside-toplevel


def voy():
    """liftoff-prepare"""
    from .voy import main as _voy

    _voy()
