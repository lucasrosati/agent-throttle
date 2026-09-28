"""agent-throttle: keep many coding agents from running heavy validation on one machine at the same time."""
import sys

if sys.version_info < (3, 11):
    sys.exit('agent-throttle needs Python 3.11 or newer (it reads TOML with tomllib).')

__version__ = '0.1.0'
