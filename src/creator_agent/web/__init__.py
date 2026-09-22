"""Local web UI for browsing collected videos (Phase 2 utility).

Serves a server-rendered browse view of synced videos - covers, click-to-play
player, metadata, stats, and the full ASR transcript. Pure stdlib, no web
framework dependency. Launch via ``creator-agent web``.
"""

from creator_agent.web.server import run_server

__all__ = ["run_server"]
