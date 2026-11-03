# Unpacman access policy

Paper-reading jobs use uploaded documents, public research sources, their own
working files, bundled instructions and installed PDF/TeX tools, and the AI
account selected by the user. They must not search personal folders, cloud drives, other projects or
other runs. Missing files are reported rather than searched for across the Mac.

Public web research is **on by default** (`permissions.network = true` in
`deslopify/harness/harness.toml`). Jobs may search for references and download
public papers into their own working directory. This does not permit access to
personal files, personal accounts or connected apps. Setting it to `false`
disables research searches/downloads and requires the companion to disclose the
resulting bibliography limit. The AI client's connection to its selected model
is separate from research access and remains enabled in either mode.

## Enforcement

- Codex commands use a named, default-deny filesystem profile, with no approval
  escalation. There is no legacy `--sandbox workspace-write` override. User
  configuration, home instruction discovery, connected apps, plugins, browser
  control and the unsandboxed image helper are disabled. An older CLI that does
  not understand the settings must fail, not run unrestricted. Codex's own
  program is readable — the folder it is started from, each link on the way,
  and its package — because Codex starts itself again to apply every file edit
  inside this sandbox; without it, every edit failed (2026-10-15).
- Codex research uses live web search. Command downloads use its network proxy
  with public domains allowed and the local/private-network guard enabled;
  direct access to local services is not granted. No upstream proxy credentials
  are inherited. Each job uses automatically assigned proxy ports so concurrent
  runs do not share a fixed listener port. This is not a research-domain
  allowlist or a guarantee that downloaded content is trustworthy.
- Claude/open-weight paper jobs use the native Bash sandbox, deny reads outside
  the run and runtime code, prohibit unsandboxed retries, and fail if the sandbox
  is unavailable. Native file tools, MCP integrations and plugins are disabled;
  file operations use sandboxed commands. These clients must support the listed
  sandbox settings. Codex was exercised locally; Claude was not installed on the
  test Mac, so its live behavior remains to be tested before relying on it.
  (Tested live on 2026-10-13, and made to work: CLAUDE.md.)
- Unrelated environment credentials, SSH-agent sockets and shell/Python startup
  overrides are not inherited. Only the selected model's authentication is added.
  Codex authentication is handled by its CLI, not exposed through the command
  filesystem. Run-specific Codex state avoids reusing previous conversations.
- Explanations/questions use a restricted, tool-free call where supported;
  their provider process is additionally confined by macOS Seatbelt.
- Server-side companion PDF compilation also uses Seatbelt, disables shell
  escape and latexmk startup files, and cannot read unrelated personal files.
  Inside an already restricted paper command, it inherits that kernel sandbox;
  an environment marker alone is not sufficient to bypass sandbox creation.
- Input snapshots reject links and special files. A run that leaves such files
  is quarantined before the server copies or reuses its output; linked targets
  are not read or removed.
- "Add to my papers" downloads connect only to checked public IP addresses,
  rechecking every redirect and retaining HTTPS certificate/hostname validation.
  When system DNS returns only VPN fake IPs in `198.18.0.0/15`, the downloader
  asks Cloudflare's encrypted DNS service at `1.1.1.1` for real public addresses.
  Only the paper website's hostname is sent, not the PDF, query, or credentials.
  This fallback does not allow other private addresses, change system DNS/VPN
  settings, or enable network access for AI commands. If public resolution or
  the connection fails, the download is refused with a specific explanation.
  Downloads do not inherit environment HTTP proxies or perform a second DNS
  lookup after the address check.

The trusted app still manages its own settings, uploads, run records and model
sign-ins. System libraries and installed tool code are necessary runtime reads,
not permission to search personal data. This is not a VM or a formal security
audit; keep the provider clients and OS updated. macOS is the tested target.
The auxiliary process sandbox refuses unsupported platforms rather than
silently running without protection.

Already-running clients retain their original launch policy. Restart the server
only after active runs finish to load the new launcher, then start a fresh run.

Offline verification (synthetic files only, no model calls):

```sh
UNPACMAN_SANDBOX_TEST=1 .venv/bin/python -m pytest tests/test_access.py -q
```

Public-network verification additionally fetches `https://example.com` (no
uploaded document or account credential is sent) and checks that synthetic
outside files and a disposable localhost service remain inaccessible:

```sh
UNPACMAN_SANDBOX_TEST=1 UNPACMAN_NETWORK_TEST=1 .venv/bin/python -m pytest tests/test_access.py -q
```

References: [Codex permission profiles](https://learn.chatgpt.com/docs/permissions)
and [Claude's command sandbox](https://code.claude.com/docs/en/sandboxing).
