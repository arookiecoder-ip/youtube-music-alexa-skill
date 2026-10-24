# Playback boundaries and validation

The current architecture is maintained in [../ARCHITECTURE.md](../ARCHITECTURE.md).

It documents the one-process authority, progressive resolver/relay, temporary current-track retention, queue/output revisions and leases, browser refresh/failover, Alexa buffered decisions and finish recovery, authentication, deployment and verification limits.

This compatibility entry point replaces the earlier checklist's superseded cache and polling descriptions. Detailed companion references remain:

- [STREAMING_AUDIO.md](STREAMING_AUDIO.md): resolver/relay, range semantics, current-track storage and streaming checks.
- [alexa-queue-reconciliation.md](alexa-queue-reconciliation.md): buffered-successor decision protocol.
- [STATUS_PANEL.md](STATUS_PANEL.md): owner diagnostics and their measurement limits.
- [../CHANGELOG.md](../CHANGELOG.md): dated changes, reversions, PR register and complete reachable commit index.
