---
name: here-now
description: Publish static files and sites to here.now for instant public URLs.
homepage: https://here.now/docs
metadata: {"hermitcrab":{"emoji":"🌐"}}
---

# here.now

Instant web hosting for agent-built static sites, documents, dashboards, prototypes, and assets.

## When to use

Use this skill when the user wants any of:
- a live URL for a generated site or artifact
- instant hosting without setting up infrastructure
- a quick shareable preview for HTML, CSS, JS, images, PDFs, or similar files
- an update to an existing here.now site

## Core model

here.now is static hosting only.

- Publish one file or a whole folder
- Anonymous publish works without an account and expires after 24 hours
- Authenticated publish uses `Authorization: Bearer <API_KEY>` for permanent sites and management APIs
- Anonymous publishes return a `claimUrl` and `claimToken`; keep them and show the `claimUrl` to the user

## Recommended workflow

1. Build a manifest of files relative to the site root
2. Create the site with `POST /api/v1/publish`
3. Upload each file to its presigned upload URL
4. Finalize with the returned `finalizeUrl`
5. Return the live `siteUrl` to the user
6. If anonymous, also return the `claimUrl` and warn that it is returned only once

## Create a site

```bash
curl -sS https://here.now/api/v1/publish \
  -H "X-HereNow-Client: hermitcrab/manual" \
  -H "Content-Type: application/json" \
  -d '{
    "files": [
      {
        "path": "index.html",
        "size": 1234,
        "contentType": "text/html; charset=utf-8"
      }
    ]
  }'
```

For authenticated publish, add:

```bash
-H "Authorization: Bearer <API_KEY>"
```

The response includes:
- `siteUrl`
- `upload.versionId`
- `upload.uploads[]`
- `upload.finalizeUrl`
- for anonymous sites: `claimUrl`, `claimToken`, `expiresAt`

## Upload files

For each item in `upload.uploads[]`, upload the matching local file:

```bash
curl -X PUT "<presigned-url>" \
  -H "Content-Type: <content-type>" \
  --data-binary @<local-file>
```

Uploads can run in parallel.

## Finalize

```bash
curl -sS -X POST "<finalize-url>" \
  -H "Content-Type: application/json" \
  -d '{"versionId":"<version-id>"}'
```

## Update an existing site

Use `PUT /api/v1/publish/:slug` with the same manifest shape.

- owned sites require `Authorization: Bearer <API_KEY>`
- anonymous sites require `claimToken` in the request body
- include file hashes when possible so unchanged files can be skipped server-side

## Authentication

Preferred key locations, first match wins:
- `--api-key` for scripted use only
- `HERENOW_API_KEY`
- `~/.herenow/credentials`

Agent-assisted sign-up:

```bash
curl -sS https://here.now/api/auth/agent/request-code \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com"}'
```

```bash
curl -sS https://here.now/api/auth/agent/verify-code \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","code":"ABCD-2345"}'
```

Store the key:

```bash
mkdir -p ~/.herenow && chmod 700 ~/.herenow
printf '%s\n' "<API_KEY>" > ~/.herenow/credentials && chmod 600 ~/.herenow/credentials
```

## Important rules

- Paths in the manifest must be relative to the site root; do not send parent folder names
- Always keep and surface anonymous `claimUrl` values to the user
- Prefer sending `X-HereNow-Client` for better service-side debugging
- Use hashes on updates when available to avoid re-uploading unchanged files
- Read the docs for advanced management flows: passwords, payment gating, domains, and links

## Reference

- Docs: https://here.now/docs
- Overview: https://here.now
