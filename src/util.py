import os.path
import sys
from datetime import timedelta


PROGRAM_NAME = os.path.basename(sys.argv[0]).rstrip(".py")


def doc_url(doc_name: str) -> str:
  """Return a URL to some piece of checked-in documentation."""
  return f"https://github.com/tstein/mirror-minder/blob/main/doc/{doc_name}.md"


def readable_timedelta(td: timedelta) -> str:
  """Format a timedelta in a less-precise but more-readable way than its built-in
  stringification."""
  days, seconds = td.days, td.seconds
  # The original td.days will have a negative sign if and only if the timedelta is
  # negative.
  sign = "-" if td.days < 0 else ""
  # Convert to an absolute, minimum value.
  if days < 0:
    # timedelta represents negative deltas as negative days + positive seconds.
    days = -(days + 1)
    seconds = 86400 - seconds

  if days == 0:
    return f"{sign}{seconds // 3600}h{round((seconds % 3600) / 60)}m"
  else:
    return f"{sign}{days}d{round(seconds / 3600)}h"
