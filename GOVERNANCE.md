# Governance

wdx-agent is a small project with a simple model.

## Roles

- **Maintainer:** [Arun Rajiah](https://www.arunrajiah.com) (@arunrajiah). Sets direction, reviews and merges pull requests, cuts releases, and has the final say when consensus cannot be reached.
- **Contributors:** anyone who opens an issue or pull request. Contributors with a sustained record of good pull requests may be invited to become committers with merge rights.

## How decisions are made

- Day to day changes: a pull request, reviewed and merged by a maintainer or committer.
- Changes to what leaves the device, to the default privacy settings, or to the event mapping: discussed in an issue first, left open for at least seven days, then decided by the maintainer with the reasons written down in the issue.
- The WDX format itself is governed in its own repository: [wildlife-detection-exchange](https://github.com/arunrajiah/wildlife-detection-exchange).

## Principles that do not change

1. The agent stays open source under Apache 2.0.
2. The agent stays dependency free and readable in one sitting.
3. The agent works with any WDX endpoint. WildNetwork is the default, never the only option.
4. Location privacy is on by default.

## Releases

Semantic versioning. The `VERSION` constant in `wdx_agent.py`, a git tag, and an entry in [CHANGELOG.md](CHANGELOG.md) for every release.

## Relationship to WildNetwork

WildNetwork (https://wildnetwork.arunrajiah.com) is the hosted map and data service that this agent sends to by default. It is a separate project by the same author. The agent and the WDX format are open so that station owners are never locked in: you can point the agent at your own server at any time by changing `endpoint`.

The names "wdx-agent", "WDX" and "WildNetwork" identify these projects. Forks are welcome under the license, but please use a different name for a modified distribution.
