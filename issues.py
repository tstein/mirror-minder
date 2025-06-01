"""Utilities for interacting with and editing issues.

Note that the bare word "repo" means a package repo in most of this codebase, but in
this file, it means a code repo."""

import datetime
import json
from typing import Optional

from sh import gh


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


def issue_title(package_repo_domain: str, repo_name: str) -> str:
  """Returns an issue title for the given package repo domain and name. This should be
  stable, and is used for determining whether there's already an issue for a problem
  with a given repo host."""
  return f"[mirror-minder] {package_repo_domain} / {repo_name} is unhealthy"


def issue_body(package_repo_domain: str, repo_name: str, reason: str) -> str:
  """Returns an issue body appropriate for notifying humans of a problem."""
  return f"""
[`mirror-minder`](https://github.com/tstein/mirror-minder) has detected an issue with the `{repo_name}` repo on `https://{package_repo_domain}`: {reason}.

Last updated: {datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S %Z")}
    """.strip()
