#!/usr/bin/env python
# /// script
# dependencies = [
#   "requests",
#   "sh",
# ]
# ///
"""Continuously monitor Termux repo mirrors for freshness. Files Github issues if the
data being vended by a mirror falls too far behind the authoritative mirrors we
initially push to, as well as if we are unable to confirm that a mirror is up to date
due to durable availability or parsing issues.

Determines the list of repos to monitor on startup and holds them forever, and does not
cache any info about mirror freshness between runs.

Major TODOs:
  * Alert if this program's is unable to maintain current knowledge of the authoritative
    repos.
  * Alert on logic bugs.
Potentially worthwhile upgrades:
  * Handle updates to the mirror list mid-run.
  * Auto-resolve the issues it creates when possible.
"""

import argparse
import logging
import os
import os.path
import time
import urllib3
from datetime import datetime, timedelta, UTC
from typing import Optional

import requests
import sh

from issues import issue_body, issue_title, open_new_issue, search_issues
from repos import (
  CHECK_INTERVAL,
  Mirror,
  MirrorGroup,
  clone_or_update_termux_tools_repo,
  load_mirrors,
  maybe_write_cache,
  next_check_time,
)
from util import readable_timedelta

############################
# hard-coded configuration #
############################
STALENESS_LIMIT = timedelta(days=3)
CONSECUTIVE_FAIL_LIMIT = round(timedelta(days=3) / CHECK_INTERVAL)
# How long to wait before giving up on retrieving a release file, end-to-end. This
# should be generous: the `main` Release is around 13 KiB, and this timeout should
# represent a so-slow-it's-basically-stalled level of throughput.
RELEASE_RETRIEVAL_LIMIT_S = 120

REPORTING_CODE_REPO = "https://github.com/tstein/mirror-minder"

#############################
# argument-controlled state #
#############################
LOG_ONLY = False


def extract_authoritative_mirrors(
  mirror_groups: list[MirrorGroup],
) -> dict[str, Mirror]:
  """Takes a list of mirror groups and extracts the authoritative mirrors for each repo.

  Returns a dict mapping repo names (e.g. "main") to the appropriate Mirrors."""
  authorities = {}
  for mirror_group in mirror_groups:
    for mirror in mirror_group.mirrors:
      if mirror.is_authoritative():
        # We need a bunch more code to do anything sensible if we believe in multiple
        # authorities.
        assert mirror.repo_name not in authorities
        authorities[mirror.repo_name] = mirror
  logging.info(f"extracted authoritative mirrors: {authorities}")
  return authorities


def check_and_update_mirror(mirror: Mirror) -> Mirror:
  """Retrieve the given mirror's release file and parse a last sync time out of it.
  Update the state in the Mirror with our success or failure. Returns the same Mirror
  object that was passed in, but only so the type checker will yell if this doesn't
  explicitly signal failure or success."""

  def fail(mirror) -> Mirror:
    mirror.last_check = datetime.now(UTC)
    mirror.consecutive_check_failures += 1
    mirror.next_check = next_check_time()
    return mirror

  def succeed(mirror, sync_time, release_url) -> Mirror:
    mirror.last_check = datetime.now(UTC)
    mirror.last_successful_check = datetime.now(UTC)
    mirror.last_sync_time = sync_time
    mirror.consecutive_check_failures = 0
    mirror.next_check = next_check_time()
    logging.info(f"successfully retrieved {release_url}")
    logging.debug(f"mirror={mirror}")
    return mirror

  release_url = mirror.release_url()
  start = time.monotonic()
  try:
    release_req = requests.get(release_url, timeout=RELEASE_RETRIEVAL_LIMIT_S)
  except requests.exceptions.ConnectionError:
    if time.monotonic() - start > RELEASE_RETRIEVAL_LIMIT_S:
      logging.error(f"connect timeout for {release_url}")
    else:
      logging.error(f"connect failure for {release_url}")
    logging.debug(f"mirror={mirror}")
    return fail(mirror)
  except (requests.exceptions.ReadTimeout, urllib3.exceptions.ReadTimeoutError):
    logging.error(f"read timeout for {release_url}")
    return fail(mirror)
  if release_req.status_code != 200:
    logging.warning(f"retrieving {release_url} returned HTTP {release_req.status_code}")
    logging.debug(f"mirror={mirror}")
    return fail(mirror)

  for line in release_req.text.splitlines():
    if line.startswith("Date:"):
      # This is a sync time. line looks like this: "Date: Tue, 3 Jun 2025 06:18:01 UTC"
      # Assumes:
      #   it's always UTC, which might not be true
      #   it uses abbreviated month names
      try:
        sync_time_str = line.strip("Date: ").rstrip(" UTC").split(", ")[1]
        sync_time = datetime.fromtimestamp(
          datetime.strptime(sync_time_str, "%d %b %Y %H:%M:%S").timestamp(), UTC
        )
        return succeed(mirror, sync_time, release_url)
      except ValueError:
        logging.exception(
          f"retrieved release file at {release_url}, but couldn't parse the sync time"
        )
        logging.debug(f"mirror={mirror}")
        return fail(mirror)

  # We didn't find a sync time in the release file. Treat this as a failure.
  logging.error(
    f"retrieved release file at {release_url}, but couldn't find a sync time"
  )
  logging.debug(f"mirror={mirror}")
  return fail(mirror)


def file_github_issue(repo_domain: str, details: str) -> None:
  """Create an issue in the configured repo, if there isn't already an open one for this
  repo."""
  title = issue_title(repo_domain)
  body = issue_body(repo_domain, details)
  if LOG_ONLY:
    logging.warning(f"would create issue, but running log-only:\n{title}\n{body}")
    return

  try:
    if url := search_issues(REPORTING_CODE_REPO, title):
      logging.info(f"found existing issue: {url}")
    else:
      issue_url = open_new_issue(REPORTING_CODE_REPO, title, body)
      logging.warning(f"created issue {issue_url}")
  except (ValueError, sh.ErrorReturnCode):
    logging.exception(
      "something went wrong communicating with github - no issue created"
    )


def judge_mirror(mirror: Mirror, authority: Optional[Mirror]) -> tuple[bool, str]:
  """Decide if a mirror looks unhealthy and return an explanation of why or why not."""
  # If we're trying to judge an authority, it's a bug.
  assert not mirror.is_authoritative()

  # We need to know if we are failing to monitor the mirror.
  if mirror.consecutive_check_failures > CONSECUTIVE_FAIL_LIMIT:
    return (
      False,
      f"⭕ retrieving it failed {mirror.consecutive_check_failures} times in a row",
    )
  if mirror.consecutive_check_failures:
    logging.debug(
      f"mirror {mirror.repo_url} has {mirror.consecutive_check_failures} "
      "consecutive check failures - not enough to alert"
    )

  # Failure to determine the sync time is counted in consecutive failures, and authority
  # freshness needs to be handled separately. It's okay do nothing if we don't have
  # enough info to do anything else where.
  if not mirror.last_sync_time or not authority or not authority.last_sync_time:
    return (
      True,
      "🟨 authority freshness unknown and failure/staleness limits not yet exceeded",
    )

  # This is the freshness check we're all here for.
  staleness = authority.last_sync_time - mirror.last_sync_time
  if staleness > STALENESS_LIMIT:
    return (
      False,
      f"⭕ hasn't synced since {mirror.last_sync_time} "
      f"(`{readable_timedelta(staleness)}` older than "
      f"[authority]({authority.repo_url}))",
    )

  return (
    True,
    f"🟢 looks good, last synced {mirror.last_sync_time} "
    f"(`{readable_timedelta(staleness)}` older than "
    f"[authority]({authority.repo_url}))",
  )


def judge_mirror_group(group: MirrorGroup, authorities: dict[str, Mirror]) -> None:
  """Decide if a mirror group looks unhealthy and do something useful if it does.

  Does nothing if we haven't at least tried to check every mirror in the group at least
  once."""
  if not all([m.last_check for m in group.mirrors]):
    logging.info(
      f"not judging mirror group at {group.domain} until all mirrors are checked at least once"
    )
    return

  group_healthy = True
  explanations: list[tuple[Mirror, str]] = []
  for mirror in group.mirrors:
    # This path judges mirrors against authorities. It does not handle authority issues.
    if mirror.is_authoritative():
      continue
    mirror_healthy, explanation = judge_mirror(
      mirror, authorities.get(mirror.repo_name)
    )
    group_healthy = group_healthy and mirror_healthy
    explanations.append((mirror, explanation))
  if group_healthy:
    return

  def p(mirror, explanation):
    return f"""
## {mirror.repo_name}

{explanation}

links: [repo root]({mirror.repo_url}), [`Release`]({mirror.release_url()})
""".strip()

  detail_parts = [p(m, e) for (m, e) in explanations]
  details = "\n".join(detail_parts)

  file_github_issue(group.domain, details)


def check_mirrors_forever(
  mirror_groups: list[MirrorGroup], authorities: dict[str, Mirror]
) -> None:
  while True:
    time.sleep(0.1)
    now = datetime.now(UTC)
    for group in mirror_groups:
      did_anything = False
      for mirror in group.mirrors:
        if mirror.next_check < now:
          _ = check_and_update_mirror(mirror)
          did_anything = True

      if did_anything:
        judge_mirror_group(group, authorities)
        maybe_write_cache(mirror_groups)


def main() -> None:
  global LOG_ONLY

  parser = argparse.ArgumentParser()
  parser.add_argument(
    "--log-only",
    action="store_true",
    default=False,
    help="don't touch the issue tracker: if a package repo is bad, just log",
  )
  parser.add_argument(
    "-v", action="store_true", default=False, help="enable debug logging"
  )
  parser.add_argument("WORKDIR")
  args = parser.parse_args()

  # No timestamp because you should be running this in a systemd unit, or piping it to
  # `ts` or something.
  log_format = "%(levelname).1s %(filename)s:%(lineno)d(%(funcName)s) %(message)s"
  log_level = logging.INFO
  if args.v:
    log_level = logging.DEBUG
  logging.basicConfig(format=log_format, level=log_level)

  if args.log_only:
    logging.warning("running in log-only mode")
    LOG_ONLY = True
  os.chdir(args.WORKDIR)

  clone_or_update_termux_tools_repo()
  mirror_groups = load_mirrors()
  authorities = extract_authoritative_mirrors(mirror_groups)

  # Authorities remain in the all-mirrors list so we can monitor them with the same
  # logic as secondaries, but we can avoid some log noise by making sure they're checked
  # first.
  for group in mirror_groups:
    for mirror in group.mirrors:
      if mirror.is_authoritative():
        mirror.next_check = datetime.fromtimestamp(0, UTC)
  check_mirrors_forever(mirror_groups, authorities)


if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    pass
