"""
JESSY Capabilities Package.

Each module here defines real, local implementations of actions JESSY
can take, and registers them into the shared capability registry.

This __init__ imports every capability module so that simply importing
`capabilities` registers everything, keeping main.py clean.
"""

from capabilities import system  # noqa: F401
