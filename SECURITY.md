# Dependency security

Use Bun **1.4.2** at the repository root for the JavaScript workspace and uv
**0.11.28** with Python **3.14** for the backend. Commit `bun.lock` and `uv.lock`
with dependency changes. Use frozen installs; review release notes, lockfile
changes and compatibility before accepting upgrades.

```sh
bun --no-env-file install --frozen-lockfile --ignore-scripts
bun --no-env-file audit --json
uv export --frozen --all-groups --no-hashes --no-emit-workspace --no-annotate -o python-audit-requirements.txt
uv tool run --no-build pip-audit==2.10.1 --strict --no-deps --disable-pip -r python-audit-requirements.txt --format json
```

The export audits exact locked third-party versions without installing the
application or running package build hooks. The auditor itself installs from
wheels. The workspace constraints preserve patched transitive version floors.
Audit all dependencies, including development and build tools, before release.
CI retains the raw Python report before applying the exceptions below.

The original RAG project recorded its own verification on 2026-10-21.
Those results are not claims about this integration. Grounded Research's current
verification is recorded separately in `docs/verification.md`; no Docker image or
paid provider smoke test was executed in this integration session.

JavaScript dependency lifecycle scripts are disabled, with an empty
`trustedDependencies` list. Review a script's source before allowing it.
For Python installs, prefer wheels. The existing `emails` dependency requires
source-only `dkimpy==1.1.8`: its reviewed setuptools build reads the README and
copies package files, command entrypoints and man pages. Its PyPI source SHA-256
is `b5f60fb47bbf5d8d762f134bcea0c388eba6b498342a682a21f1686545094b77`.
Build only that reviewed source in isolation if a wheel is needed; this is not
blanket approval for other source builds or future dkimpy versions.

## Temporary Chroma exceptions

On **2026-10-26**, auditing the locked Chroma **1.5.9** dependency returned
these four advisories. The **2026-11-21** review of the locked dependency graph
and the embedded-client deployment boundary confirmed the same four aliases,
with no fix versions reported by the audit response:

| Exact exception | PyPI identifier | Impact in Chroma server deployments |
| --- | --- | --- |
| [GHSA-f4j7-r4q5-qw2c](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c) | PYSEC-2026-311 | Critical: model repository code execution through the collection creation HTTP endpoint |
| [GHSA-36p7-vc44-83pf](https://github.com/advisories/GHSA-36p7-vc44-83pf) | PYSEC-2026-3814 | Critical: model repository code execution through the collection update HTTP endpoint |
| [GHSA-2wm9-hf6c-p5cr](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr) | PYSEC-2026-3813 | High: authenticated cross-tenant collection access |
| [GHSA-xph7-9rjv-w5fr](https://github.com/advisories/GHSA-xph7-9rjv-w5fr) | PYSEC-2026-3815 | High: SimpleRBACAuthorizationProvider ignores resource scope |

PyPI returns five records because PYSEC-2026-311 appears twice. CI permits only
the four exact GHSA aliases and fails on other findings. These exceptions must
be reviewed again by **2026-12-21**; CI rejects them after that date.

The current application constructs `Chroma` in
`backend/app/services/rag_service.py` with a local `persist_directory` and
explicit `OpenAIEmbeddings`. The locked LangChain wrapper selects
`chromadb.PersistentClient`, whose default implementation is local
`RustBindingsAPI`. The application generates collection names from UUIDs and
does not accept model repository configurations or `trust_remote_code`.
Compose and the backend entrypoint start the application API, without a Chroma
HTTP server. The Chroma server authentication and SimpleRBAC providers are not
configured in this deployment.

This bounds the reviewed exposure; it does not establish that Chroma has no
other vulnerabilities. The exceptions become invalid before adding a Chroma
server, remote client, server authorization provider, attacker-controlled model
configuration, or an environment override such as `CHROMA_API_IMPL`. Local
configuration and persisted files must remain trusted. Recheck upstream fixes
and this deployment boundary at the review date or whenever that boundary
changes.

## JavaScript advisory review, 2026-10-29

The Seroval advisories discovered during this iteration are fixed by the exact
workspace override `seroval=1.6.11`. Python `dnspython` and `langgraph-sdk` are
also constrained to their patched versions. Frozen installs and all regression
suites must pass after these lockfile changes.

The native Bun report still lists one low-severity advisory for `deepmerge@4.3.1`,
[GHSA-gcqx-w48m-w2h4](https://github.com/advisories/GHSA-gcqx-w48m-w2h4).
The official advisory and npm registry had no patched release on 2026-10-29.
The reviewed path is `packages/react-email` → `@react-email/render` →
`html-to-text` → `deepmerge`. Its `compile()` merges converter options and
selector configurations supplied by the local template code; it does not merge
an untrusted JSON options object from the application API. Email templates are
locally authored. The Python backend serves the already exported templates,
and the browser build does not expose this options-merging API.

This is an exact, temporary reachability exception, not a claim of zero
JavaScript vulnerabilities. `scripts/check-js-audit.py` permits only this
package, advisory URL and low severity, retains the raw native report, rejects
unrecognized/error reports and expires the exception after **2026-11-29**.
It is invalid before accepting converter options, selector objects, formatter
configuration or arbitrary template code from an untrusted source. Recheck the
upstream fix before the review deadline or any change to that boundary.

## Publication review, 2026-11-21

The native audit identified new advisories in the locked JavaScript and Python
graphs. JavaScript overrides now select `follow-redirects=1.16.3`, `glob=14.0.6`,
`minimatch=11.0.9` and `next=16.4.6`. The latter three are also used by the local
React Email tooling, so the email export is checked alongside the browser build.
Lifecycle scripts remain disabled during installation.

Python constraint floors and the lockfile now select patched `dnspython=2.9.2`,
`oauthlib=4.0.1`, `opentelemetry-api=1.45.2` and `pypdf=6.21.4`, together with the
matching OpenTelemetry SDK. The existing four exact Chroma exceptions above
remain limited to the reviewed local embedded-client deployment.
