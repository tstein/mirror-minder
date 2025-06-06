# what to do when `mirror-minder` complains about a mirror

`mirror-minder`'s issues are meant to include as much context as possible on what looks
wrong, what it does and doesn't know, and where you might want to look next. Each issue
represents problems with one or more mirrors on a single mirror host, identified by its
FQDN. Each mirror is given one of three judgments, plus an explanation:

* A hollow red circle (⭕) means the repo had an issue that definitely requires attention.
* A yellow square (🟨) means the repo has an issue that doesn't yet require attention,
  or that `mirror-minder` doesn't have enough info to assert a problem. An issue with
  yellow squares but no hollow red circles is not actionable, and is either a bug in
  mirror-minder or represents an issue that should be closed and hasn't been yet.
* A full green circle (🟢) means the repo passed all checks.

The intent is that `mirror-minder` only alerts humans once it's unambiguously time for
humans to act, so it is immediately appropriate to @ the mirror operator as soon as an
issue is opened. You can look up whom to contact in the [`termux-packages`
wiki](https://github.com/termux/termux-packages/wiki/Mirrors).

If we cannot reach them, or they cannot restore their mirror to availability and
freshness, the only move is to remove it from the mirror lists. We do not currently have
a standard for how long to wait for a response before disabling a mirror. If you have an
interesting case, mention it in your nearest dev chat so we can develop one.

To remove a mirror, send a PR to
[`termux/termux-tools`](https://github.com/termux/termux-tools) that:
1) Deletes the mirror definition from the
[`mirrors/`](https://github.com/termux/termux-tools/tree/master/mirrors) directory.
2) Removes the deleted mirror from the regional `pkgdata_` variables, and adds it to
`pkgdata_MIRRORS_REMOVED`, in
[`mirrors/Makefile.am`](https://github.com/termux/termux-tools/blob/master/mirrors/Makefile.am).
