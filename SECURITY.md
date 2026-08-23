# Security

This is a **sample** library. The code demonstrates patterns against Microsoft Foundry and is
not intended to run unmodified in production. Review authentication, network isolation and
data handling against your own requirements before adapting anything here.

## Reporting a problem with this repository

If you find a security issue in **this sample code** — for example a pattern that leaks a
credential, or guidance that would lead someone into an insecure configuration — please open
an issue in this repository, or contact the maintainer privately through GitHub if you would
rather not disclose it publicly first.

This project is not an official Microsoft product and is not covered by Microsoft's support
or vulnerability-response programmes.

## Reporting a vulnerability in a Microsoft product

If you believe you have found a vulnerability in **Microsoft Foundry, Azure or any other
Microsoft product or service** — as opposed to this sample — do not report it here. Report it
to the Microsoft Security Response Center:

- <https://msrc.microsoft.com/create-report>
- or email [secure@microsoft.com](mailto:secure@microsoft.com)

Please do not report vulnerabilities in Microsoft products through public GitHub issues.

## Handling secrets in this repo

Configuration is read from `.env`, which is gitignored. `.env.example` carries placeholders
only. Never commit real endpoints, keys, subscription keys or tenant identifiers.

Every pattern here authenticates with Microsoft Entra ID via `DefaultAzureCredential` by
default, so a normal run needs no static secrets at all. Key-based fallbacks exist for a few
patterns; prefer the keyless path.
