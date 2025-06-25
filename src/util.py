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
  if td.days > 0:
    return f"{td.days}d{round(td.seconds / 3600)}h"
  return f"{td.seconds // 3600}h{round((td.seconds % 3600) / 60)}m"
