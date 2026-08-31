from __future__ import annotations

import json
from pathlib import Path

from creator_agent.models.creator import Creator
from creator_agent.models.transcript import Transcript
from creator_agent.models.video import Video
from creator_agent.storage.naming import sanitize_name, video_filename


class FileStorage:
    """On-disk storage for creator profiles and video assets.

    Layout::

        {base}/{creator_nickname}/
            profile.json
            avatar.jpg
            videos/{video.storage_path}/
                {title}_{date}.mp4
                cover.jpg
                metadata.json

    The creator folder is the sanitized nickname; the per-video folder
    (``video.storage_path``, e.g. ``2026-07-11_张三``) is assigned by the
    repository so all three saves for a video land in the same place.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)

    # -- paths --------------------------------------------------------------
    # ``_path`` variants do NOT create directories (safe for read checks);
    # ``_dir`` variants ensure the directory exists (for writes).

    def _creator_path(self, creator: Creator) -> Path:
        return self._base / sanitize_name(creator.nickname, creator.id)

    def _creator_dir(self, creator: Creator) -> Path:
        d = self._creator_path(creator)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _video_folder(self, video: Video) -> str:
        # storage_path is assigned by the repository; fall back to video.id for
        # rows created before that field existed.
        return video.storage_path or video.id

    def _video_path(self, creator: Creator, video: Video) -> Path:
        return self._creator_path(creator) / self._video_folder(video)

    def _video_dir(self, creator: Creator, video: Video) -> Path:
        d = self._video_path(creator, video)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- creator-level ------------------------------------------------------

    def save_creator_profile(self, creator: Creator) -> Path:
        path = self._creator_dir(creator) / 'profile.json'
        data = creator.model_dump(mode='json')
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    def load_creator_profile(self, creator: Creator) -> Creator | None:
        path = self._creator_path(creator) / 'profile.json'
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        return Creator(**data)

    def save_avatar(self, creator: Creator, data: bytes) -> Path:
        path = self._creator_dir(creator) / 'avatar.jpg'
        path.write_bytes(data)
        return path

    # -- video-level --------------------------------------------------------

    def save_video_file(self, creator: Creator, video: Video, data: bytes) -> Path:
        path = self._video_dir(creator, video) / video_filename(video.title, video.published_at)
        path.write_bytes(data)
        return path

    def save_cover(self, creator: Creator, video: Video, data: bytes) -> Path:
        path = self._video_dir(creator, video) / 'cover.jpg'
        path.write_bytes(data)
        return path

    def save_metadata(self, creator: Creator, video: Video) -> Path:
        path = self._video_dir(creator, video) / 'metadata.json'
        data = video.model_dump(mode='json')
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    def load_metadata(self, creator: Creator, video: Video) -> Video | None:
        path = self._video_path(creator, video) / 'metadata.json'
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        return Video(**data)

    def list_videos(self, creator: Creator) -> list[str]:
        d = self._creator_path(creator)
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir())

    def video_exists(self, creator: Creator, video: Video) -> bool:
        return (self._video_path(creator, video) / 'metadata.json').exists()

    # -- ASR transcript + audio ------------------------------------------------

    def video_file_path(self, creator: Creator, video: Video) -> Path:
        """Path to the downloaded video file (does not create dirs)."""
        return self._video_path(creator, video) / video_filename(video.title, video.published_at)

    def audio_path(self, creator: Creator, video: Video) -> Path:
        """Path to the 16kHz mono WAV file (same base name as video, .wav)."""
        from creator_agent.storage.naming import video_filename
        mp4_name = video_filename(video.title, video.published_at)
        wav_name = mp4_name.replace('.mp4', '.wav')
        return self._video_path(creator, video) / wav_name

    def save_transcript(self, creator: Creator, video: Video, transcript: Transcript) -> Path:
        path = self._video_dir(creator, video) / 'transcript.json'
        path.write_text(transcript.model_dump_json(indent=2), encoding='utf-8')
        return path

    def load_transcript(self, creator: Creator, video: Video) -> Transcript | None:
        path = self._video_path(creator, video) / 'transcript.json'
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        return Transcript(**data)

    def save_txt(self, creator: Creator, video: Video, text: str) -> Path:
        path = self._video_dir(creator, video) / 'transcript.txt'
        path.write_text(text, encoding='utf-8')
        return path
