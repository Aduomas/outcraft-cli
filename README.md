# Outcraft

Unofficial Outcraft tools for agents to read and manage campaigns. This repo currently contains the Outcraft CLI; an MCP server has not been added yet.

## Login

Use `OUTCRAFT_EMAIL` and `OUTCRAFT_PASSWORD` in your environment or a private `.env` file. Google sign-in is not automated; an existing signed-in browser session can be reused.

## Run

Requires Python 3.11 or newer.

```bash
./outcraft --env-file .env auth login
./outcraft campaigns list
./outcraft --help
```

## Current limitation

Existing-session reads are verified. Outcraft currently blocks direct write requests with Cloudflare HTTP 403, so campaign editing is not working end to end yet. Password login has not been verified live.
