# Contributing

This is a sample library. Issues and pull requests are welcome — especially fixes where a
pattern has drifted from the current Foundry SDK surface, which moves quickly.

## Before you open a PR

- **Run the pattern you changed.** Every pattern in this repo is meant to execute against a
  real Foundry project. If it can't be run, say so plainly in the pattern's `TALK-TRACK.md`
  rather than describing it as if it does.
- **Keep secrets out.** Configuration belongs in `.env` (gitignored). `.env.example` holds
  placeholders only — never a real endpoint, key or tenant ID.
- **Update the docs next to the code.** A pattern is its folder: the script, its
  `TALK-TRACK.md`, its row in the README, and its `.env.example` entries.

## Contributor License Agreement

None required. Contributions are accepted under the terms of the repository's
[MIT licence](LICENSE) — by opening a pull request you agree your contribution is licensed
the same way.

## Trademarks

This project is **not** an official Microsoft product and is not endorsed by Microsoft. It
references Microsoft product names and shows Microsoft Foundry in diagrams and screenshots
for identification purposes only. Microsoft, Azure, Microsoft Foundry, Microsoft 365 and
related names are trademarks of the Microsoft group of companies; any use of Microsoft
trademarks or logos is subject to
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Third-party names and marks — including those of other cloud providers referenced as
examples — are the property of their respective owners.
