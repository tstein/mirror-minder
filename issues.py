import json
from typing import Optional

from sh import gh


def list_open_issues(repo: str) -> dict[str, str]:
  """Return a dict of open issue titles to open issue urls in the given repo. Raises on
  any error."""
  # gh really, really wants to print rich text. _tty_out=False prevents this.
  issues_json = gh(
    "issue", "list", "-L", "9999", "-R", repo, "--json", "title,url", _tty_out=False
  )
  issues = json.loads(issues_json)
  return {i["title"]: i["url"] for i in issues}


def search_issues(repo: str, title: str) -> Optional[str]:
  """Search for an open issue with the given title in the given repo. Returns a URL to
  the most recent issue matching the title, or None if no issues match."""
  issues_json = gh(
    "issue",
    "list",
    "-R",
    repo,
    "--json",
    "title,url,createdAt",
    "--search",
    f"is:open {title}",
    _tty_out=False,
  )
  issues = json.loads(issues_json)
  if not issues:
    return None

  issues.sort(key=lambda i: i["createdAt"], reverse=True)
  return issues[0]["url"]


def open_new_issue(repo: str, title: str, body: str) -> str:
  """Create a new issue in the given repo, unconditionally. Raises on any error.

  Returns the URL of the new issue."""
  output = gh("issue", "create", "-R", repo, "-t", title, "-b", body)
  return output.splitlines()[-1]
