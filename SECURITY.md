# Security Policy

## Reporting a vulnerability

Please do not disclose security vulnerabilities in public issues. Report them
privately to the repository owner through GitHub's private vulnerability
reporting feature.

Include the affected component, reproduction steps, impact, and any suggested
mitigation. Please avoid including real API keys, access tokens, passwords, or
personal data in the report.

## Secrets

This project reads credentials from a local `.env` file. Never commit that
file. Use `.env.example` as the public configuration template and rotate any
credential that may have been exposed.
