# Open questions (ask the user before deciding)

- **Docker (deferred by the user, decision 27):** ship the server image as `docker\chessfish-server.tar` (`docker save` +
  `load-and-run.sh`) or as a build context (`build: ./server`)? Docker isn't installed on this PC, which favours the build context.
  Still to do with it: `server/Dockerfile`, compose/.env templates, the wizard's "Docker files" page.
- **Visual theme** preference for the GUI/website? Currently dark GUI + dark site with lichess-brown board.
- **Installer acceptance still open:** an all-users install and a run on a clean Windows user/VM (only per-user was tested here).

## Resolved
See DECISIONS.md 1–39.
