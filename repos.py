import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import Optional

from sh import git

# Approximately how long to wait after startup before the initial checks of each mirror.
INITIAL_CHECK_DELAY = timedelta(seconds=30)
# Approximately how long to wait between checks after the initial round.
CHECK_INTERVAL = timedelta(minutes=30)
CHECK_JITTER_FRACTION = 0.05

TERMUX_TOOLS_REPO = "termux-tools"
TERMUX_TOOLS_REPO_URL = f"https://github.com/termux/{TERMUX_TOOLS_REPO}"


@dataclass
class Mirror:
  """Represents a single mirror of a single package repo. Different repos (in apt terms)
  are different mirrors (in terms of this class).

  e.g., there is one Mirror object for https://packages-cf.termux.dev/apt/termux-main."""

  # Static info about the mirror.
  repo_url: str
  # The name of the package repo. e.g. main, root, x11
  repo_name: str
  weight: int

  # Mirror-checking state.
  next_check: datetime
  # Number of times in a row we've failed to successfully get the release file and parse
  # a sync time.
  consecutive_check_failures: int
  # The last time we attempted to check the mirror. None means we have never tried.
  last_check: Optional[datetime]
  # The last time we were able to get and parse the mirror's release file. None means we
  # have never done that.
  last_successful_check: Optional[datetime]
  # The last sync time reported by the last successful pull and parse of the mirror's
  # release file. None means we have never done that.
  last_sync_time: Optional[datetime]

  @property
  def domain(self) -> str:
    return self.repo_url.split("/")[2]

  def is_authoritative(self) -> bool:
    """Mirror freshness needs to be determined against an authoritative mirror, not the
    current wall time - it's normal for repos to sometimes go longer than the staleness
    limit without there being any new data.

    All mirrors are equal from a client perpective, so this program has secret knowledge
    of which mirrors are authoritative."""
    return self.repo_url.startswith("https://packages.termux.dev")

  def release_url(self) -> str:
    """Returns the full URL of the Release file for this repo."""
    repo_path = "stable" if self.repo_name == "main" else self.repo_name
    return f"{self.repo_url}/dists/{repo_path}/Release"


@dataclass
class MirrorGroup:
  """Represents a group of mirrors behind the same domain. Failures, and particularly
  failures-to-monitor, are likely to be correlated among repos hosted in the same place,
  so it's useful to keep them together."""

  domain: str
  mirrors: list[Mirror]


def next_check_time(delay: Optional[timedelta] = None) -> datetime:
  """Chooses a time to check something. Jittered.

  If no delay is passed, defaults to the configured interval."""
  if not delay:
    delay = CHECK_INTERVAL

  # Choose a jitter factor in [-1, 1).
  jitter = ((random.random() * 2) - 1) * (delay * CHECK_JITTER_FRACTION)
  return datetime.now(UTC) + delay + jitter


def clone_or_update_termux_tools_repo() -> None:
  """Call while in the workdir. We have a recent commit of termux-tools after this
  returns."""
  if os.path.exists(TERMUX_TOOLS_REPO):
    os.chdir(TERMUX_TOOLS_REPO)
    git("clean", "-dfx")
    git("pull")
  else:
    git("clone", TERMUX_TOOLS_REPO_URL)
    os.chdir(TERMUX_TOOLS_REPO)
  os.chdir("..")


def load_mirrors_from_file(domain: str, filepath: str) -> MirrorGroup:
  """Creates Mirrors for each repo in the mirror definition file at the given path."""
  mirrors: list[Mirror] = []
  repos: dict[str, str] = {}
  weight = -1
  with open(filepath) as f:
    for line in f:
      if line.strip().startswith("#"):
        continue
      var, val = line.strip().split("=")
      match var:
        case "WEIGHT":
          weight = int(val)
        case _:
          repos[var.lower()] = val.strip('"')
  for repo_name, repo_url in repos.items():
    mirrors.append(
      Mirror(
        repo_url=repo_url,
        repo_name=repo_name,
        weight=int(weight),
        next_check=next_check_time(INITIAL_CHECK_DELAY),
        consecutive_check_failures=0,
        last_check=None,
        last_successful_check=None,
        last_sync_time=None,
      )
    )
  logging.debug(f"loaded from {filepath}, mirrors={mirrors}")
  return MirrorGroup(domain, mirrors)


def load_mirrors_from_repo() -> list[MirrorGroup]:
  """Creates Mirrors in MirrorGroups for each (termux package) repo in the (termux-tools
  git) repo. Assumes $PWD contains the (termux-tools git) repo."""
  mirror_groups: list[MirrorGroup] = []
  mirror_dir = f"{TERMUX_TOOLS_REPO}/mirrors"
  for group in os.listdir(mirror_dir):
    group_dir = f"{mirror_dir}/{group}"
    if not os.path.isdir(group_dir):
      continue
    for domain in os.listdir(group_dir):
      mirror_file = f"{group_dir}/{domain}"
      mirror_groups.append(load_mirrors_from_file(domain, mirror_file))
  mirror_count = sum([len(m_g.mirrors) for m_g in mirror_groups])
  logging.info(
    f"loaded {len(mirror_groups)} mirror groups, {mirror_count} mirrors from repo"
  )
  logging.debug(f"mirror_groups={mirror_groups}")
  return mirror_groups
