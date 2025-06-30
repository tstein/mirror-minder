"""Utilities for interacting with and editing issues.

Note that the bare word "repo" means a package repo in most of this codebase, but in
this file, it means a code repo."""

import datetime
import json
from typing import Optional

from sh import gh

from repos import TERMUX_TOOLS_REPO_URL
from util import PROGRAM_NAME, doc_url


def search_issues(repo: str, title: str) -> Optional[str]:
  """Search for an open issue with the given title in the given repo. Returns a URL to
  the most recent issue matching the title, or None if no issues match."""
  # fmt: off
  issues_json = gh(
    "issue", "list", "-R", repo,
    "--json", "title,url,createdAt",
    "--search", f"is:open {title}",
    _tty_out=False # disables color, so we don't have to deal with those escapes
  )
  # fmt: on
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


def update_issue(url: str, body: str) -> None:
  """Updates the body of an existing issue. Raises on any error."""
  gh("issue", "edit", url, "-b", body)


def close_issue(url: str) -> None:
  """Closes an existing issue. Does nothing if the issue is already closed. Raises on
  any error."""
  gh("issue", "close", url)


def issue_title(package_repo_domain: str) -> str:
  """Returns an issue title for the given package repo domain. This should be stable,
  and is used for determining whether there's already an issue for a problem with a
  given repo host."""
  return f"mirrors: {package_repo_domain} is unhealthy"


def issue_body(package_repo_domain: str, mirror_file_path: str, details: str) -> str:
  """Returns an issue body appropriate for notifying humans of a problem."""
  return f"""
[`{PROGRAM_NAME}`🤖](https://github.com/tstein/mirror-minder) has detected an issue with \
the package repo(s) on [`{package_repo_domain}`](https://{package_repo_domain}).

* [playbook for `{PROGRAM_NAME}` issues]({doc_url("playbook")})
* [definition file for all repos on `{package_repo_domain}`]({TERMUX_TOOLS_REPO_URL}/tree/master/{mirror_file_path})

last updated: {datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S %Z")}

{details}
""".strip()
