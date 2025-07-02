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
"""

import argparse
import logging
import os
import os.path
import random
import time
from datetime import datetime, timedelta, UTC
from typing import Optional

import requests
import sh

from issues import (
  close_issue,
  issue_body,
  issue_title,
  open_new_issue,
  search_issues,
  update_issue,
)
from repos import (
  CHECK_INTERVAL,
  TERMUX_TOOLS_REPO_URL,
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
# Approximately how frequently to stop monitoring, update the mirror defs from the repo,
# and start monitoring again.
MONITOR_PERIOD_S = 24 * 3600  # 1 day
# How fresh we want mirrors to be. We don't need to alert the instant a mirror is this
# stale, but it's helpful to surface which side of this line each mriror is on.
FRESHNESS_TARGET = timedelta(hours=6)
# When a mirror's state is this much older than the authoritative mirror for its repo,
# we want to alert.
STALENESS_LIMIT = timedelta(days=3)
# If an authority gets an update, we won't know how stale any particular mirror was
# before that update. If it's been long enough between authority updates, well-behaved
# mirrors will immediately look stale. Give them a chance to pick up new states before
# alerting.
AUTHORITY_UPDATE_GRACE_PERIOD = timedelta(hours=12)
# If we go this many attempts to monitor any mirror in a row without successfully
# connecting, retrieving, and extracting the most recent sync time, we want to alert.
CONSECUTIVE_FAIL_LIMIT = round(timedelta(days=3) / CHECK_INTERVAL)
# How long to wait before giving up on retrieving a release file, end-to-end. This
# should be generous: the `main` Release is around 13 KiB, and this timeout should
# represent a so-slow-it's-basically-stalled level of throughput.
RELEASE_RETRIEVAL_LIMIT_S = 120
# Whether we should automatically close open issues when the mirrors they cover go green.
AUTO_CLOSE = False

REPORTING_CODE_REPO = TERMUX_TOOLS_REPO_URL

# Headers to use in all HTTP requests.
BASE_HEADERS = {"User-Agent": "Debian APT-HTTP/1.3 (0.0.0+really-mirror-minder)"}

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
    release_req = requests.get(
      release_url, headers=BASE_HEADERS, timeout=RELEASE_RETRIEVAL_LIMIT_S
    )
  except requests.exceptions.ConnectionError:
    if time.monotonic() - start > RELEASE_RETRIEVAL_LIMIT_S:
      logging.error(f"connect timeout for {release_url}")
    else:
      logging.error(f"connect failure for {release_url}")
    logging.debug(f"mirror={mirror}")
    return fail(mirror)
  except requests.exceptions.ReadTimeout:
    logging.error(f"read timeout for {release_url}")
    return fail(mirror)
  except requests.exceptions.ChunkedEncodingError:
    logging.error(f"incomplete read for {release_url}")
    return fail(mirror)
  except requests.exceptions.RequestException:
    logging.exception(f"unhandled requests exception for {release_url}")
    return fail(mirror)

  if release_req.status_code != 200:
    logging.warning(f"retrieving {release_url} returned HTTP {release_req.status_code}")
    logging.debug(f"mirror={mirror}")
    return fail(mirror)

  for line in release_req.text.splitlines():
    if line.startswith("Date:"):
      # This is a sync time. line looks like this: "Date: Tue, 3 Jun 2025 06:18:01 UTC"
      # Assumes:
      #   it is explicitly in UTC
      #   it uses abbreviated month names
      try:
        sync_time_str = line.strip("Date: ").split(", ")[1]
        # strptime() can understand a TZ name of "UTC", but that produces a naive
        # (non-offset-aware) datetime, and this program deals entirely in offset-aware
        # datetimes. By replacing the string UTC with a numerical offset and using %z
        # instead of %Z, we get an offset-aware TZ.
        if sync_time_str.endswith(" UTC"):
          sync_time_str = sync_time_str.replace(" UTC", "+0000")
        else:
          logging.error(f"sync time for {release_url} not in UTC: {sync_time_str}")
          continue
        sync_time = datetime.strptime(sync_time_str, "%d %b %Y %H:%M:%S%z")
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


def update_github_issue(
  repo_domain: str, mirror_file_path: str, details: str, create: bool
) -> None:
  """Create or update an issue in the configured repo with the latest problem details on
  a repo."""
  title = issue_title(repo_domain)
  body = issue_body(repo_domain, mirror_file_path, details)
  if LOG_ONLY:
    logging.warning(f"would update issue, but running log-only:\n{title}\n{body}")
    return

  try:
    if url := search_issues(REPORTING_CODE_REPO, title):
      update_issue(url, body)
      logging.info(f"updated existing issue: {url}")
    elif create:
      issue_url = open_new_issue(REPORTING_CODE_REPO, title, body)
      logging.warning(f"created issue {issue_url}")
  except (ValueError, sh.ErrorReturnCode):
    logging.exception("something went wrong communicating with github")


def close_github_issue(repo_domain: str, mirror_file_path: str, details: str) -> None:
  """Close an issue in the configured repo, updating the details one last time before
  doing so."""
  title = issue_title(repo_domain)
  body = issue_body(repo_domain, mirror_file_path, details)
  if LOG_ONLY:
    logging.warning(f"would close issue, but running log-only:\n{title}\n{body}")
    return

  try:
    if url := search_issues(REPORTING_CODE_REPO, title):
      update_issue(url, body)
      close_issue(url)
      logging.info(f"closed issue: {url}")
  except (ValueError, sh.ErrorReturnCode):
    logging.exception("something went wrong communicating with github")


def judge_mirror(
  mirror: Mirror, authority: Optional[Mirror]
) -> tuple[Optional[bool], str]:
  """Decide if a mirror looks unhealthy and return an explanation of why or why not.

  Returns Optional[bool] for each mirror because the decision here is trinary - the
  mirror is healthy, unhealthy, or indeterminate."""
  # If we're trying to judge an authority, it's a bug.
  assert not mirror.is_authoritative()

  # We need to know if we are failing to monitor the mirror.
  if mirror.consecutive_check_failures >= CONSECUTIVE_FAIL_LIMIT:
    return (
      False,
      f"⭕ retrieving it failed {mirror.consecutive_check_failures} times in a row, "
      f"last successful retrieval was {mirror.last_successful_check or '`<never>`'}",
    )
  if mirror.consecutive_check_failures:
    alert_eta = (
      CONSECUTIVE_FAIL_LIMIT - mirror.consecutive_check_failures
    ) * CHECK_INTERVAL
    logging.info(
      f"mirror {mirror.repo_url} has {mirror.consecutive_check_failures} "
      f"consecutive check failures - will alert in about {readable_timedelta(alert_eta)}"
    )
    return (
      None,
      f"🟨 retrieving it failed {mirror.consecutive_check_failures} times in a row "
      f"(about `{readable_timedelta(alert_eta)}` until it exceeds the unavailability "
      "limit) - last successful retrieval was "
      f"{mirror.last_successful_check or '`<never>`'}",
    )

  # Both of these cases should be prevented by ordering and filtering above this
  # function, and it's a bug if we take either of these branches, but we can do
  # something sensible in that case.
  # 1. Mirror has never been checked. Assumes mirror.consecutive_check_failures > 0 has
  # been exhaustively handled above.
  if not mirror.last_sync_time:
    return (
      None,
      "⁉️ mirror has never been checked (and it's a bug if you're seeing this)",
    )
  # 2. Authority freshness needs to be handled separately.
  if not authority or not authority.last_sync_time:
    return (
      None,
      "⁉️ authority freshness unknown (and it's a bug if you're seeing this)",
    )

  # This is the freshness check we're all here for.
  staleness = authority.last_sync_time - mirror.last_sync_time
  authority_age = datetime.now(UTC) - authority.last_sync_time
  logging.info(
    f"{mirror.repo_url}: staleness={staleness}, "
    f"authority_age={readable_timedelta(authority_age)}, "
    f"last_sync={mirror.last_sync_time}, authority_last_sync={authority.last_sync_time}"
  )
  if staleness > STALENESS_LIMIT:
    # When an authority updates infrequentely relative to the staleness limit, diligent
    # mirrors will appear stale the moment it does update. Not helpful to make noise in
    # this situation - give mirrors time to update.
    if authority_age < AUTHORITY_UPDATE_GRACE_PERIOD:
      return (
        None,
        f"🟨 (in grace period) hasn't synced since {mirror.last_sync_time}: "
        f"`{readable_timedelta(staleness)}` older than "
        f"[its authority]({authority.repo_url}), but its authority was updated only "
        f"`{readable_timedelta(authority_age)}` ago",
      )
    else:
      return (
        False,
        f"⭕ hasn't synced since {mirror.last_sync_time}: "
        f"`{readable_timedelta(staleness)}` older than "
        f"[its authority]({authority.repo_url}), which was updated "
        f"`{readable_timedelta(authority_age)}` ago",
      )
  elif staleness > FRESHNESS_TARGET:
    return (
      True,
      f"🟨 (below alert threshold) hasn't synced since {mirror.last_sync_time}: "
      f"`{readable_timedelta(staleness)}` older than "
      f"[its authority]({authority.repo_url}), which was updated "
      f"`{readable_timedelta(authority_age)}` ago",
    )
  else:
    # No problem at all.
    return (
      True,
      f"🟢 looks good, last synced {mirror.last_sync_time}: "
      f"`{readable_timedelta(staleness)}` older than "
      f"[its authority]({authority.repo_url}), which was updated "
      f"`{readable_timedelta(authority_age)}` ago",
    )


def judge_mirror_group(group: MirrorGroup, authorities: dict[str, Mirror]) -> None:
  """Decide if a mirror group looks unhealthy and do something useful if it does.

  Does nothing if we haven't attempted to check every mirror in the group recently."""

  def is_recent(last_check) -> bool:
    if last_check is None:
      return False
    return datetime.now(UTC) - last_check < (2 * CHECK_INTERVAL)

  if not all([is_recent(m.last_check) for m in group.mirrors]):
    logging.info(
      f"not judging mirror group at {group.domain} until all mirrors have been checked "
      "recently"
    )
    return

  explanations: list[tuple[Mirror, Optional[bool], str]] = []
  for mirror in group.mirrors:
    # This path judges mirrors against authorities. It does not handle authority issues.
    if mirror.is_authoritative():
      continue
    mirror_healthy, explanation = judge_mirror(
      mirror, authorities.get(mirror.repo_name)
    )
    explanations.append((mirror, mirror_healthy, explanation))
  if not explanations:
    logging.info(f"not judging group for {group.domain}")
    return

  any_red = any([mirror_healthy is False for _, mirror_healthy, _ in explanations])
  all_green = all([mirror_healthy is True for _, mirror_healthy, _ in explanations])
  short_mirror_health = ", ".join(
    [f"{m.repo_name} health={h}" for m, h, _ in explanations]
  )
  logging.info(
    f"judged group for {group.domain}, any_red={any_red}, all_green={all_green}: {short_mirror_health}"
  )

  def p(mirror, explanation):
    return f"""
## {mirror.repo_name}

{explanation}

links: [repo root]({mirror.repo_url}), [`Release`]({mirror.release_url()})
""".strip()

  detail_parts = [p(m, e) for (m, _, e) in explanations]
  details = "\n".join(detail_parts)

  # If everything looks good and auto-closure is enabled, close any open issue.
  # Otherwise, if there's any red, create-or-update, and if it's green+yellow, update
  # only if it already exists.
  if all_green and AUTO_CLOSE:
    close_github_issue(group.domain, group.mirror_file_path, details)
  else:
    update_github_issue(group.domain, group.mirror_file_path, details, create=any_red)


def monitor_mirrors_for_a_while(monitor_period_s: float) -> None:
  """Update the mirror definitions, load the cache, and then monitor mirrors for a
  while. No is state persisted between calls to this function except via the cache."""
  # Load and process mirrors.
  clone_or_update_termux_tools_repo()
  mirror_groups = load_mirrors()
  authorities = extract_authoritative_mirrors(mirror_groups)
  # Authorities remain in the all-mirrors list so we can monitor them with the same
  # logic as secondaries, but we can avoid some log noise by making sure they're checked
  # first.
  for group in mirror_groups:
    for mirror in group.mirrors:
      if mirror.is_authoritative():
        logging.info(
          f"identified authority for repo_name={mirror.repo_name}, repo_url={mirror.repo_url}"
        )
        mirror.next_check = datetime.fromtimestamp(0, UTC)

  # Enter a finite loop of mirror monitoring.
  start = time.monotonic()
  while time.monotonic() - start < monitor_period_s:
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

  # Write the cache one more time, just for cleanliness.
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

  while True:
    # Choose a jitter fraction in [-.1, .1).
    monitor_period_s = round(
      MONITOR_PERIOD_S + ((random.random() * 2) - 1) * (MONITOR_PERIOD_S / 10)
    )
    logging.info(f"monitoring mirrors for {monitor_period_s} seconds")
    monitor_mirrors_for_a_while(monitor_period_s)


if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    pass
