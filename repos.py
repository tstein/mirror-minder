import logging
import os
import pickle
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from itertools import chain
from typing import Optional, Self

from sh import git

from util import PROGRAM_NAME

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
  # If you change this, don't forget to also change .update_from().
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

  def update_from(self, other: Self) -> None:
    """Load all mirror-checking state from the other mirror object into this one."""
    # Ignore other.next_check, so longer delays from previous runs don't override
    # shorter delays before the initial checks on this one.
    self.consecutive_check_failures = other.consecutive_check_failures
    self.last_check = other.last_check
    self.last_successful_check = other.last_successful_check
    self.last_sync_time = other.last_sync_time

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


def __load_mirrors_from_file(domain: str, filepath: str) -> MirrorGroup:
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
        repo_url=repo_url.rstrip("/"),
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


def _load_mirrors_from_repo() -> list[MirrorGroup]:
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
      mirror_groups.append(__load_mirrors_from_file(domain, mirror_file))
  mirror_count = sum([len(m_g.mirrors) for m_g in mirror_groups])
  logging.info(
    f"loaded {len(mirror_groups)} mirror groups, {mirror_count} mirrors from repo"
  )
  logging.debug(f"mirror_groups={mirror_groups}")
  return mirror_groups


def _get_usable_cache_path() -> str:
  """Returns a path to a cache file, and if it doesn't exist, creates any necessary
  parent directories for it to be immediately writable."""
  cache_dir = os.path.expanduser(f"~/.cache/{PROGRAM_NAME}")
  os.makedirs(cache_dir, exist_ok=True)
  return f"{cache_dir}/mirror_cache"


def _load_mirrors_from_cache() -> Optional[list[MirrorGroup]]:
  """Creates Mirrors in MirrorGroups from the cache file, if any exists. Returns None if
  there is no cache file or if failing to read or understand it fails for any reason."""
  cache_path = _get_usable_cache_path()

  try:
    with open(cache_path, "rb") as f:
      groups = pickle.load(f)
  except FileNotFoundError:
    logging.info(f"no cache file at {cache_path}, starting fresh")
    return None
  except pickle.UnpicklingError:
    logging.exception(f"failed to unpickle {cache_path}; starting fresh")
    return None

  # Sanity check the object we loaded so we can fail fast if it's wrong.
  if not isinstance(groups, list) or not all(
    [isinstance(o, MirrorGroup) for o in groups]
  ):
    logging.error(
      "unpickled successfully, but what we unpickled wasn't list[MirrorGroup]: "
      f"{groups}"
    )
    logging.error("starting fresh")
    return None

  mirror_count = sum([len(g.mirrors) for g in groups])
  logging.info(
    f"loaded {mirror_count} mirrors in {len(groups)} groups from {cache_path}"
  )
  return groups


def maybe_write_cache(groups: list[MirrorGroup]) -> None:
  """Write the groups to the cache file if it's time to do so. Whether it's time to do
  is decided interally by this function."""
  cache_path = _get_usable_cache_path()
  with open(cache_path, "wb") as f:
    pickle.dump(groups, f)


def load_mirrors() -> list[MirrorGroup]:
  """Creates Mirrors in MirrorGroups for each (termux package) repo in the (termux-tools
  git) repo, loading all useful info in the mirror cache in the process. Assumes $PWD
  contains the (termux-tools git) repo."""
  from_repo = _load_mirrors_from_repo()
  if not (from_cache := _load_mirrors_from_cache()):
    return from_repo

  def mirror_map(groups: list[MirrorGroup]) -> dict[str, Mirror]:
    """Returns a map from URLs to Mirrors corresponding to the given list of
    MirrorGroups."""
    return {m.repo_url: m for m in chain.from_iterable([g.mirrors for g in groups])}

  repo_map = mirror_map(from_repo)
  cache_map = mirror_map(from_cache)

  for url, repo_mirror in repo_map.items():
    if cache_mirror := cache_map.get(url):
      repo_mirror.update_from(cache_mirror)

  repo_urls = set(repo_map.keys())
  cache_urls = set(cache_map.keys())
  mirrors_added = repo_urls - cache_urls
  mirrors_removed = cache_urls - repo_urls
  logging.info(
    f"merged cache, {len(mirrors_added)} mirrors added, {len(mirrors_removed)} "
    "removed since last cache update"
  )
  logging.debug(f"mirrors_added={mirrors_added}")
  logging.debug(f"mirrors_removed={mirrors_removed}")
  return from_repo
