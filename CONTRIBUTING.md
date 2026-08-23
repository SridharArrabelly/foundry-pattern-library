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

Most contributions require you to agree to a Contributor License Agreement (CLA) declaring
that you have the right to, and actually do, grant us the rights to use your contribution.
For details, visit <https://cla.opensource.microsoft.com>.

When you submit a pull request, a CLA bot will automatically determine whether you need to
provide a CLA and decorate the PR appropriately. Simply follow the instructions provided by
the bot. You will only need to do this once across all repos using our CLA.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized
use of Microsoft trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause
confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos is
subject to those third parties' policies.
