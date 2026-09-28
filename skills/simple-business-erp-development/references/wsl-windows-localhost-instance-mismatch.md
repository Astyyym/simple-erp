# User-visible ERP instance verification

Use this when source changes appear correct in WSL but the Windows browser still shows an older ERP page.

## Failure signature

- WSL-local request shows the new button/route.
- User screenshot from Windows lacks it.
- Both appear to use `127.0.0.1:5000`.
- `/health` may be identical because version was not bumped.

## Root cause class

Different processes can answer “the same” localhost URL from different sides. Commonly, a packaged desktop ERP runs Waitress on Windows while a source Flask server runs inside WSL. Localhost forwarding and stale relay/process ownership can make WSL and Windows receive different HTML.

## Deterministic diagnosis

1. In WSL, inspect the port listener and process working directory.
2. In Windows PowerShell, inspect `Get-NetTCPConnection` and owning process/path.
3. Fetch the affected page from both sides.
4. Test for a unique newly introduced marker—not generic title/version/health output.
5. Compare response `Server` headers where useful: packaged production commonly reports `waitress`; Flask development commonly reports `Werkzeug`.

## Safe recovery

- Stop the stale packaged/relay process when appropriate, or avoid collision.
- Start source preview on another port and bind to `0.0.0.0`.
- Obtain the current WSL IP.
- Fetch `http://<wsl-ip>:<new-port>/<affected-route>` from Windows PowerShell and confirm the unique marker.
- Only then send that exact URL to the user.

## Communication rule

When the user's screenshot contradicts internal browser output, the screenshot proves what the user sees. Say so immediately. Do not send them through repeated refresh/restart cycles or imply they overlooked the control. State whether the code is still being integrated, whether the wrong instance is served, and which URL was verified from Windows.
