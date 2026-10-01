# Security

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead:
**Security tab → Report a vulnerability** on https://github.com/arunrajiah/wdx-agent.

You should get a first reply within seven days. Fixes are released as a new version with a note in the changelog, and reporters are credited unless they prefer otherwise.

## What matters most here

- Anything that sends more than the documented fields off the device.
- Anything that leaks or weakens the device API key.
- Anything in `install.sh` that runs with more privilege than it needs.
- Any way for a server response to make the agent execute code or write outside its state file.

## Design notes

- The agent opens detection databases read-only and writes only its own state file.
- The API key is stored in `/etc/wdx-agent.ini` with mode 640.
- Keys are restricted to one source system on the server side and can be revoked.
