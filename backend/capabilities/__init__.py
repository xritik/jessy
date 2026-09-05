"""
JESSY Capabilities Package.

Importing this package registers every capability module into the
shared registry, keeping main.py clean.
"""

from capabilities import system      # noqa: F401
from capabilities import filesystem  # noqa: F401
from capabilities import windows     # noqa: F401
from capabilities import process     # noqa: F401
