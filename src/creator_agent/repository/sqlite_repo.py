from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from creator_agent.models.creator import Creator
from creator_agent.models.video import CollectedVideo, Video, VideoStats, VideoStatus


class Repository:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS creator (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                platform_uid TEXT NOT NULL,
                nickname TEXT NOT NULL,
                homepage_url TEXT NOT NULL,
                avatar_url TEXT,
                bio TEXT,
                added_at TEXT NOT NULL,
                last_synced_at TEXT,
                sync_status TEXT NOT NULL DEFAULT 'idle',
                last_error TEXT,
                storage_path TEXT NOT NULL,
                UNIQUE(platform, platform_uid)
            );

            CREATE TABLE IF NOT EXISTS video (
                id TEXT PRIMARY KEY,
                creator_id TEXT NOT NULL REFERENCES creator(id),
                platform TEXT NOT NULL,
                platform_vid TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                cover_url TEXT,
                video_url TEXT,
                published_at TEXT NOT NULL,
                duration_sec INTEGER,
                stats TEXT DEFAULT '{}',
                tags TEXT DEFAULT '[]',
                collected_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'NEW',
                storage_path TEXT NOT NULL,
                UNIQUE(platform, platform_vid)
            );

            CREATE TABLE IF NOT EXISTS sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id TEXT NOT NULL REFERENCES creator(id),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                videos_collected INTEGER DEFAULT 0,
                videos_downloaded INTEGER DEFAULT 0,
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_video_creator ON video(creator_id);
            CREATE INDEX IF NOT EXISTS idx_video_status ON video(status);
            CREATE INDEX IF NOT EXISTS idx_video_published ON video(published_at DESC);
        """)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add_creator(self, creator: Creator) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO creator
               (id, platform, platform_uid, nickname, homepage_url, avatar_url, bio,
                added_at, last_synced_at, sync_status, last_error, storage_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                creator.id,
                creator.platform,
                creator.platform_uid,
                creator.nickname,
                str(creator.homepage_url),
                str(creator.avatar_url) if creator.avatar_url else None,
                creator.bio,
                creator.added_at.isoformat(),
                creator.last_synced_at.isoformat() if creator.last_synced_at else None,
                creator.sync_status,
                creator.last_error,
                creator.id,
            ),
        )
        self._conn.commit()

    def get_creator(self, creator_id: str) -> Creator | None:
        row = self._conn.execute("SELECT * FROM creator WHERE id = ?", (creator_id,)).fetchone()
        return self._row_to_creator(row) if row else None

    def list_creators(self) -> list[Creator]:
        rows = self._conn.execute("SELECT * FROM creator ORDER BY added_at DESC").fetchall()
        return [self._row_to_creator(r) for r in rows]

    def update_creator_sync(
        self,
        creator_id: str,
        status: str,
        synced_at: datetime,
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE creator SET sync_status = ?, last_synced_at = ?, last_error = ? WHERE id = ?",
            (status, synced_at.isoformat(), error, creator_id),
        )
        self._conn.commit()

    def remove_creator(self, creator_id: str) -> None:
        self._conn.execute("DELETE FROM video WHERE creator_id = ?", (creator_id,))
        self._conn.execute("DELETE FROM sync_history WHERE creator_id = ?", (creator_id,))
        self._conn.execute("DELETE FROM creator WHERE id = ?", (creator_id,))
        self._conn.commit()

    def upsert_collected(self, creator: Creator, cv: CollectedVideo) -> Video:
        video_id = f"{cv.platform}_{cv.platform_vid}"
        now = datetime.now(UTC)
        stats_json = cv.stats.model_dump_json()
        tags_json = json.dumps(cv.hashtags, ensure_ascii=False)
        storage_path = f"{creator.id}/videos/{video_id}"

        existing = self._conn.execute(
            "SELECT * FROM video WHERE platform = ? AND platform_vid = ?",
            (cv.platform, cv.platform_vid),
        ).fetchone()

        if existing:
            self._conn.execute(
                """UPDATE video SET
                   title = ?, description = ?, cover_url = ?, video_url = ?,
                   published_at = ?, stats = ?, tags = ?
                   WHERE id = ?""",
                (
                    cv.title,
                    cv.description,
                    str(cv.cover_url) if cv.cover_url else None,
                    str(cv.video_url) if cv.video_url else None,
                    cv.published_at.isoformat(),
                    stats_json,
                    tags_json,
                    existing["id"],
                ),
            )
            self._conn.commit()
            return self.get_video(existing["id"])

        self._conn.execute(
            """INSERT INTO video
               (id, creator_id, platform, platform_vid, title, description,
                cover_url, video_url, published_at, duration_sec,
                stats, tags, collected_at, status, storage_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                video_id,
                creator.id,
                cv.platform,
                cv.platform_vid,
                cv.title,
                cv.description,
                str(cv.cover_url) if cv.cover_url else None,
                str(cv.video_url) if cv.video_url else None,
                cv.published_at.isoformat(),
                None,
                stats_json,
                tags_json,
                now.isoformat(),
                VideoStatus.NEW.value,
                storage_path,
            ),
        )
        self._conn.commit()
        return self.get_video(video_id)

    def get_video(self, video_id: str) -> Video | None:
        row = self._conn.execute("SELECT * FROM video WHERE id = ?", (video_id,)).fetchone()
        return self._row_to_video(row) if row else None

    def list_videos_by_status(self, statuses: list[VideoStatus]) -> list[Video]:
        placeholders = ",".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT * FROM video WHERE status IN ({placeholders}) ORDER BY published_at DESC",
            [s.value for s in statuses],
        ).fetchall()
        return [self._row_to_video(r) for r in rows]

    def list_videos(
        self,
        creator_id: str | None = None,
        since: datetime | None = None,
    ) -> list[Video]:
        conditions = []
        params: list = []
        if creator_id:
            conditions.append("creator_id = ?")
            params.append(creator_id)
        if since:
            conditions.append("published_at >= ?")
            params.append(since.isoformat())
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self._conn.execute(f"SELECT * FROM video {where} ORDER BY published_at DESC", params).fetchall()
        return [self._row_to_video(r) for r in rows]

    def advance_status(self, video_id: str, new_status: VideoStatus) -> None:
        self._conn.execute(
            "UPDATE video SET status = ? WHERE id = ?",
            (new_status.value, video_id),
        )
        self._conn.commit()

    def start_sync(self, creator_id: str) -> int:
        now = datetime.now(UTC)
        cur = self._conn.execute(
            "INSERT INTO sync_history (creator_id, started_at, status) VALUES (?, ?, ?)",
            (creator_id, now.isoformat(), "running"),
        )
        self._conn.commit()
        return cur.lastrowid

    def finish_sync(
        self,
        sync_id: int,
        status: str,
        videos_collected: int,
        videos_downloaded: int,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self._conn.execute(
            """UPDATE sync_history SET finished_at = ?, status = ?,
               videos_collected = ?, videos_downloaded = ?, error = ?
               WHERE id = ?""",
            (now.isoformat(), status, videos_collected, videos_downloaded, error, sync_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_creator(row: sqlite3.Row) -> Creator:
        return Creator(
            id=row["id"],
            platform=row["platform"],
            platform_uid=row["platform_uid"],
            nickname=row["nickname"],
            homepage_url=row["homepage_url"],
            avatar_url=row["avatar_url"],
            bio=row["bio"],
            added_at=datetime.fromisoformat(row["added_at"]),
            last_synced_at=datetime.fromisoformat(row["last_synced_at"]) if row["last_synced_at"] else None,
            sync_status=row["sync_status"],
            last_error=row["last_error"],
        )

    @staticmethod
    def _row_to_video(row: sqlite3.Row) -> Video:
        stats_raw = row["stats"]
        stats = VideoStats(**json.loads(stats_raw)) if stats_raw else VideoStats()
        tags_raw = row["tags"]
        tags = json.loads(tags_raw) if tags_raw else []
        return Video(
            id=row["id"],
            creator_id=row["creator_id"],
            platform=row["platform"],
            platform_vid=row["platform_vid"],
            title=row["title"],
            description=row["description"] or "",
            cover_url=row["cover_url"],
            video_url=row["video_url"],
            published_at=datetime.fromisoformat(row["published_at"]),
            duration_sec=row["duration_sec"],
            stats=stats,
            tags=tags,
            collected_at=datetime.fromisoformat(row["collected_at"]),
            status=VideoStatus(row["status"]),
        )
