## setting up `mirror-minder`

`mirror-minder` is designed to be run as a service in a systemd session, and preferably
a systemd user session. To get it running on a new machine, clone this repo, and follow
these instructions:

1. Install dependencies:
  * [`uv`](https://docs.astral.sh/uv/): It may be available through your system package
    manager.
2. Decide what user you want to run this as, and grant that user linger permissions.
   (`sudo loginctl enable-linger $USERNAME`)
3. Copy `mirror-minder.service` into that user's systemd config.
   (`~$USERNAME/.config/systemd/user/`)
4. Edit the copy of that file so the ExecStart line reflects the directory you cloned
   mirror-minder into, and the directory you want it use for its workdir.
5. Create an environment file (`~$USERNAME/.config/systemd/user/mirror-minder.env`) and
   add `GH_TOKEN=` with a github access token. This should be a fine-grained personal
   access token:
   * set the resource owner to the Termux org
   * choose an expiration date, and set multiple reminders to rotate it before then
   * select only the repo you will report issues to
   * grant read and write access to issues on the repo
6. As the target user, `systemctl --user daemon-reload && systemctl --user enable --now
   mirror-minder`
7. As the target user, `systemctl --user status mirror-minder` should immediately show
   output describing useful work.


A few things to be aware of:

* `mirror-minder` assumes it is the only copy of itself in the world, and has no
  explicit behavior for the possibility that two mirror-minders might be reporting
  issues into the same repo.
