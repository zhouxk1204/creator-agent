"""Xiaohongshu (小红书) note collector implementation.

Douyin's collector walks a creator homepage and pages through the video grid;
Xiaohongshu is entered from a shared note link instead, so this package provides
the note-page resolver (:mod:`meta`) and the share-link parser (:mod:`url`) that
:meth:`creator_agent.pipeline.runner.PipelineRunner.sync_video_url` dispatches to.
A creator-grid collector can be added later behind the same ``Collector`` ABC.
"""
