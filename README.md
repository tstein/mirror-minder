`mirror-minder` is a service that monitors Termux package repos for freshness and
reports issues on github if a repo is not being updated regularly, or if it is unable to
confirm that the repo *is* being updated regularly. The basic expectations are that
repos sync from the initial Termux mirror at least once every six hours, and that all
mirrors allow access from at least one place on the internet, from which we can
centrally monitor them.

While running, modifying, or interpreting the issues created by this program, keep some
basics of monitoring a global HTTP server in mind:
  * Being able to reach a server from one place doesn't mean you can reach it from
    everywhere.
  * Being *un*able to reach a server from one place doesn't meant you *can't* reach it
    from any particular other place.
  * A fantastic or terrible connection to a server from one place doesn't necessarily
    tell you anything about the connections others will experience.
  * It is possible for a server to return different contents to clients based on where
    they're connecting from. This is unlikely for mirrors implemented as plain HTTP
    servers serving their local copies of repos, a little more likely for CDN-fronted
    mirrors, and technically possible at all times.
