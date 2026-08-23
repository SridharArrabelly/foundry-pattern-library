# Security

Microsoft takes the security of our software products and services seriously, which includes
all source code repositories managed through our GitHub organizations.

If you believe you have found a security vulnerability, please report it to us as described
below.

## Reporting security issues

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, report them to the Microsoft Security Response Center (MSRC) at
<https://msrc.microsoft.com/create-report>, or by email to
[secure@microsoft.com](mailto:secure@microsoft.com). If possible, encrypt your message with
our PGP key; please download it from the
[Microsoft Security Response Center PGP Key page](https://www.microsoft.com/msrc/pgp-key-msrc).

You should receive a response within 24 hours. If for some reason you do not, please follow
up via email to ensure we received your original message.

Please include as much of the following as you can:

- Type of issue (for example: buffer overflow, SQL injection, cross-site scripting)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code, if possible
- Impact of the issue, including how an attacker might exploit it

This information will help us triage your report more quickly.

## A note on this repository

This is a **sample** library. The code demonstrates patterns and is not intended to run
unmodified in production. In particular, review authentication, network isolation and data
handling against your own requirements before adapting anything here.

Configuration is read from `.env`, which is gitignored. Never commit real endpoints, keys or
tenant identifiers — `.env.example` carries placeholders only.

## Policy

Microsoft follows the principle of
[Coordinated Vulnerability Disclosure](https://www.microsoft.com/msrc/cvd).
