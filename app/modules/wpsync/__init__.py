"""WordPress bidirectional sync (Phase 4).

Keeps the app's ``users`` and the WordPress user base in sync **both ways over the
WP REST API only** (never MySQL), with phone as the join key, last-modified-wins
conflict resolution, and structural loop-prevention. All network I/O runs in Celery
workers, off the request path.

Module boundary: this module never touches the ``users`` table directly — every
read/write goes through ``identity.service`` (the table's owner). Identity, in turn,
enqueues an outbound push via this module's ``service.enqueue_push_user`` seam,
mirroring how it calls ``notifications.service`` for OTP SMS.
"""
