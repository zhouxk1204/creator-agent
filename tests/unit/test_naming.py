from __future__ import annotations

from datetime import UTC, datetime

from creator_agent.storage.naming import (
    cn_date_str,
    sanitize_name,
    video_folder_base,
    video_folder_name,
)


def test_sanitize_replaces_illegal_and_collapses_whitespace():
    assert sanitize_name("Test Creator") == "Test_Creator"
    assert sanitize_name("a/b:c?d*e<f>g") == "a_b_c_d_e_f_g"
    assert sanitize_name("  spaced  out  ") == "spaced_out"


def test_sanitize_strips_trailing_dots_and_underscores():
    # Windows forbids trailing dots; strip them.
    assert sanitize_name("name.") == "name"
    assert sanitize_name("name___") == "name"


def test_sanitize_falls_back_when_empty():
    assert sanitize_name("") == "unknown"
    assert sanitize_name("???") == "unknown"
    assert sanitize_name("", fallback="creator_id") == "creator_id"


def test_cn_date_str_converts_utc_to_china_day():
    # 2024-06-15 22:00 UTC -> 2024-06-16 06:00 CN.
    assert cn_date_str(datetime(2024, 6, 15, 22, 0, tzinfo=UTC)) == "2024-06-16"
    # 2024-06-15 00:00 UTC -> 2024-06-15 08:00 CN.
    assert cn_date_str(datetime(2024, 6, 15, 0, 0, tzinfo=UTC)) == "2024-06-15"


def test_video_folder_base_combines_date_and_sanitized_nickname():
    base = video_folder_base("Test Creator", datetime(2024, 6, 15, 0, 0, tzinfo=UTC))
    assert base == "2024-06-15_Test_Creator"


def test_video_folder_name_suffix_logic():
    dt = datetime(2024, 6, 15, 0, 0, tzinfo=UTC)
    assert video_folder_name("Test Creator", dt, 0) == "2024-06-15_Test_Creator"
    assert video_folder_name("Test Creator", dt, 1) == "2024-06-15_Test_Creator_2"
    assert video_folder_name("Test Creator", dt, 2) == "2024-06-15_Test_Creator_3"
