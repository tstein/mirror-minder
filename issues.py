import json
from sh import gh


def list_open_issues(repo: str) -> dict[str, str]:
  """Return a dict of open issue titles to open issue urls in the given repo. Raises on
  any error."""
  # gh really, really wants to print rich text. _tty_out=False prevents this.
  issues_json = gh("issue", "list", "-R", repo, "--json", "title,url", _tty_out=False)
  issues = json.loads(issues_json)
  return {i["title"]: i["url"] for i in issues}


def open_new_issue(repo, title, body) -> str:
  """Create a new issue in the given repo, unconditionally. Raises on any error.

  Returns the URL of the new issue."""
  output = gh("issue", "create", "-R", repo, "-t", title, "-b", body)
  return output.splitlines()[-1]
