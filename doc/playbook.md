# when `mirror-minder` complains about a mirror

## how to read these issues

`mirror-minder`'s issues are meant to include as much context as possible on what looks
wrong, what it does and doesn't know, and where you might want to look next. Each issue
represents problems with one or more mirrors on a single mirror host, identified by its
FQDN. Each mirror is given one of three judgments, plus an explanation:

* A hollow red circle (⭕) means the repo had an issue that definitely requires attention.
* A yellow square (🟨) means the repo has an issue that doesn't yet require attention,
  or that `mirror-minder` doesn't have enough info to assert a problem.
* A full green circle (🟢) means the repo passed all checks.

## what to do

The intent is that `mirror-minder` only alerts humans once it's unambiguously time for
humans to act, so it is immediately appropriate to @ the mirror operator as soon as you
see an issue with at least one red mirror. You can look up whom to contact in the
[Mirrors page of the `termux-packages`
wiki](https://github.com/termux/termux-packages/wiki/Mirrors).

An issue should never be opened without at least one definite problem, but it's possible
for an issue that was correctly opened to change to all yellow or all green+yellow,
particularly if the authority updates and the stale repo enters a grace period as a
result. Check the edit history on the issue, the state of the repo, and use your
judgment to decide whether to contact the operator, to wait, or to close the bug.
(`mirror-minder` will open a new issue immediately if a yellow mirror that you thought
was going to go green turns red instead.)

## if the operator fixes the issue

`mirror-minder` updates the initial comment of its issues continuously, and will
auto-resolve the issues it opens on the next check if the all the mirrors on a domain
turn healthy.

## if the operator can't or won't fix the issue

If we cannot reach the operator, or they cannot restore their mirror to availability and
freshness, the only option is to remove it from the mirror lists. We do not currently
have a standard for how long to wait for a response before disabling a mirror. If you
have an interesting case, mention it in your nearest dev chat so we can develop one.

To remove a mirror, send a PR to
[`termux/termux-tools`](https://github.com/termux/termux-tools) that:
1) Deletes the mirror definition from the
[`mirrors/`](https://github.com/termux/termux-tools/tree/master/mirrors) directory.
2) Removes the deleted mirror from the regional `pkgdata_` variables, and adds it to
`pkgdata_MIRRORS_REMOVED`, in
[`mirrors/Makefile.am`](https://github.com/termux/termux-tools/blob/master/mirrors/Makefile.am).
3) Remove the mirror from the [Mirrors page of the `termux-packages`
wiki](https://github.com/termux/termux-packages/wiki/Mirrors). If you do not have
permissions to do this, you will need to ask someone who does.
