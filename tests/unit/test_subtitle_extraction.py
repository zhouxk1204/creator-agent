"""Exercise subtitle detection, grouping, OCR orchestration and SRT generation."""

import csv
import json
import logging
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from creator_agent.subtitle_extractor import analysis, cli
from creator_agent.subtitle_extractor.analysis import AnalysisResult, analyze_video
from creator_agent.subtitle_extractor.crop import CropConfig, CropRegion
from creator_agent.subtitle_extractor.detection import (
    DetectionConfig,
    DetectionError,
    detect_subtitle,
    mask_iou,
    sharpness,
)
from creator_agent.subtitle_extractor.extraction import (
    ExtractedGroup,
    ExtractionConfig,
    ExtractionError,
    SubtitleExtractor,
    caption_similarity,
    clean_caption,
    is_meaningful_text,
    normalize_caption,
)
from creator_agent.subtitle_extractor.grouping import GroupingConfig, GroupTracker, SubtitleGroup
from creator_agent.subtitle_extractor.ocr import OcrReading, _parse_pipeline_results, normalize_language
from creator_agent.subtitle_extractor.reprocess import analyze_crops
from creator_agent.subtitle_extractor.srt import SrtCue, build_srt, format_timestamp
from creator_agent.subtitle_extractor.timeline import VideoAnalysisError

FRAME_SIZE = (480, 270)
CROP_CONFIG = CropConfig(region=CropRegion(5, 72, 95, 97))


def make_frame(text: str | None = None) -> Image.Image:
    image = np.full((FRAME_SIZE[1], FRAME_SIZE[0], 3), 30, np.uint8)
    cv2.rectangle(image, (0, FRAME_SIZE[1] - 70), (FRAME_SIZE[0], FRAME_SIZE[1]), (90, 120, 90), -1)
    if text:
        cv2.putText(
            image,
            text,
            (60, FRAME_SIZE[1] - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.1,
            (255, 255, 255),
            3,
            cv2.LINE_AA,
        )
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


def crop_of(frame: Image.Image) -> np.ndarray:
    rgb = np.asarray(frame.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)[194:262, 24:456]


def test_detector_finds_text_and_rejects_blank_background():
    text = detect_subtitle(crop_of(make_frame("AAAAA")))
    blank = detect_subtitle(crop_of(make_frame()))
    assert text.has_subtitle and text.mask is not None
    assert text.score >= 0.7 and text.components >= 1
    assert not blank.has_subtitle and blank.mask is None and blank.score < 0.2
    assert text.bbox is not None and text.bbox[2] > 0 and text.bbox[3] > 0


def test_detector_threshold_is_configurable():
    crop = crop_of(make_frame("AAAAA"))
    strict = DetectionConfig(threshold=0.99)
    assert not detect_subtitle(crop, strict).has_subtitle


def test_detection_weights_must_sum_to_one():
    with pytest.raises(DetectionError, match="weights"):
        DetectionConfig(bright_weight=0.8, density_weight=0.5, component_weight=0.2)
    with pytest.raises(DetectionError, match="threshold"):
        DetectionConfig(threshold=0)


def test_mask_iou_scales_between_disjoint_and_identical():
    first = detect_subtitle(crop_of(make_frame("AAAAA"))).mask
    second = detect_subtitle(crop_of(make_frame("BBBBB"))).mask
    assert first is not None and second is not None
    assert mask_iou(first, first) == 1.0
    assert mask_iou(first, second) < 0.5
    assert mask_iou(first, np.zeros_like(first)) == 0.0


def test_sharpness_prefers_crisp_text_over_blurred_text():
    sharp = crop_of(make_frame("AAAAA"))
    blurred = cv2.GaussianBlur(sharp, (15, 15), 0)
    mask = detect_subtitle(sharp).mask
    assert sharpness(sharp, mask) > sharpness(blurred, mask)


def test_grouping_merges_identical_masks_and_splits_changed_text():
    first = detect_subtitle(crop_of(make_frame("AAAAA"))).mask
    second = detect_subtitle(crop_of(make_frame("BBBBB"))).mask
    events = []
    tracker = GroupTracker(GroupingConfig(), events.append)
    tracker.add(0, first, Image.new("RGB", (20, 10)), 1.0)
    tracker.add(1, first, Image.new("RGB", (20, 10)), 2.0)
    tracker.add(2, second, Image.new("RGB", (20, 10)), 3.0)
    tracker.add(3, second, Image.new("RGB", (20, 10)), 4.0)
    tracker.finish()
    assert [(event.start_frame, event.end_frame, event.frame_count) for event in events] == [(0, 1, 2), (2, 3, 2)]
    assert events[0].selected_frames == [0, 1]


def test_grouping_tolerates_short_detection_gaps():
    first = detect_subtitle(crop_of(make_frame("AAAAA"))).mask
    events = []
    tracker = GroupTracker(GroupingConfig(max_missing_frames=2), events.append)
    tracker.add(0, first, Image.new("RGB", (20, 10)), 1.0)
    tracker.skip(1)
    tracker.skip(2)
    tracker.add(3, first, Image.new("RGB", (20, 10)), 2.0)
    tracker.skip(4)
    tracker.skip(5)
    tracker.finish()
    assert len(events) == 1
    assert (events[0].start_frame, events[0].end_frame, events[0].frame_count) == (0, 3, 2)


def test_grouping_closes_after_too_many_missing_frames():
    first = detect_subtitle(crop_of(make_frame("AAAAA"))).mask
    events = []
    tracker = GroupTracker(GroupingConfig(max_missing_frames=1), events.append)
    tracker.add(0, first, Image.new("RGB", (20, 10)), 1.0)
    tracker.add(1, first, Image.new("RGB", (20, 10)), 2.0)
    tracker.skip(2)
    tracker.skip(3)
    assert len(events) == 1
    assert (events[0].start_frame, events[0].end_frame) == (0, 1)


def test_grouping_drops_groups_below_minimum_length():
    first = detect_subtitle(crop_of(make_frame("AAAAA"))).mask
    events = []
    tracker = GroupTracker(GroupingConfig(max_missing_frames=0, min_group_frames=3), events.append)
    tracker.add(0, first, Image.new("RGB", (20, 10)), 1.0)
    tracker.skip(1)
    assert events == []


@pytest.mark.parametrize("value", [0, -0.1, 1.5, float("nan")])
def test_grouping_rejects_invalid_threshold(value):
    with pytest.raises(Exception, match="similarity"):
        GroupingConfig(similarity_threshold=value)


def test_format_timestamp_and_negative_rejection():
    assert format_timestamp(0) == "00:00:00,000"
    assert format_timestamp(1.2345) == "00:00:01,234"
    assert format_timestamp(3661.999) == "01:01:01,999"
    with pytest.raises(Exception, match="negative"):
        format_timestamp(-0.5)


def test_build_srt_orders_cues_and_clamps_overlaps():
    cues = [SrtCue(0.2, 0.9, "second", 2), SrtCue(0.0, 0.3, "first", 1)]
    text = build_srt(cues)
    assert text.startswith("1\n00:00:00,000 --> 00:00:00,200\nfirst\n")
    assert "2\n00:00:00,200 --> 00:00:00,900\nsecond\n" in text
    assert build_srt([SrtCue(1.0, 1.0, "zero", 1)]).startswith("1\n00:00:01,000 --> 00:00:01,040")


def test_ocr_reading_parsing_and_language_aliases():
    reading = _parse_pipeline_results([{"rec_texts": ["你好", ""], "rec_scores": [0.9, 0.1]}], "fake")
    assert reading.text == "你好" and reading.confidence == pytest.approx(0.9) and reading.box_count == 1
    assert normalize_language("ja") == "japan"
    assert normalize_language("zh") == "ch"
    assert normalize_language("JAPAN") == "japan"


class FakeFrame:
    def __init__(self, pts, image, duration=40, *, time_base=Fraction(1, 1000)):
        self.pts = pts
        self.duration = duration
        self.time_base = time_base
        self.width, self.height = image.size
        self.is_corrupt = False
        self._image = image

    def to_image(self):
        return self._image


class FakeContainer:
    def __init__(self, frames):
        self.frames = frames
        self.duration = 4000
        self.stream = SimpleNamespace(
            time_base=Fraction(1, 1000),
            average_rate=Fraction(25),
            start_time=0,
            duration=4,
            frames=len(frames),
            codec_context=SimpleNamespace(width=FRAME_SIZE[0], height=FRAME_SIZE[1], name="fake_codec"),
            index=0,
        )
        self.streams = SimpleNamespace(video=[self.stream])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def decode(self, stream):
        yield from self.frames


class FakeEngine:
    name = "fake"

    def __init__(self, readings):
        self.readings = list(readings)
        self.calls = []

    def read(self, image):
        self.calls.append(np.asarray(image.convert("RGB")).copy())
        reading = self.readings.pop(0)
        if isinstance(reading, Exception):
            raise reading
        return reading


def build_container(texts):
    frames = []
    for index, text in enumerate(texts):
        frames.append(FakeFrame(index * 40, make_frame(text)))
    return FakeContainer(frames)


@pytest.fixture
def input_path(tmp_path):
    path = tmp_path / "episode.mp4"
    path.write_bytes(b"not a real video; the decoder is mocked")
    return path


@pytest.fixture
def install_decoder(monkeypatch):
    def install(texts):
        container = build_container(texts)
        monkeypatch.setattr(
            analysis, "_load_av", lambda: SimpleNamespace(open=lambda path: container, time_base=1000000)
        )
        return container

    return install


def test_extraction_produces_groups_srt_and_debug_outputs(input_path, tmp_path, install_decoder):
    install_decoder(["AAAAA", "AAAAA", "AAAAA", None, None, "BBBBB", "BBBBB", None])
    output = tmp_path / "task"
    engine = FakeEngine([OcrReading("AAAAA", 0.99, "fake", 1)] * 3 + [OcrReading("BBBBB", 0.98, "fake", 1)] * 2)
    analyze_video(
        input_path,
        output_dir=output,
        save_frames=False,
        crop_config=CROP_CONFIG,
        extraction_config=ExtractionConfig(confidence_threshold=0.9, min_cue_duration=0.05),
        ocr_engine=engine,
    )
    assert len(engine.calls) == 5
    srt = (output / "result.srt").read_text(encoding="utf-8")
    assert srt == ("1\n00:00:00,000 --> 00:00:00,120\nAAAAA\n\n2\n00:00:00,200 --> 00:00:00,280\nBBBBB\n")
    groups = json.loads((output / "groups" / "groups.json").read_text(encoding="utf-8"))
    assert [group["status"] for group in groups] == ["ok", "ok"]
    assert groups[0]["start_frame"] == 0 and groups[0]["end_frame"] == 2
    assert groups[1]["start_frame"] == 5 and groups[1]["end_frame"] == 6
    detection_rows = list(csv.DictReader((output / "detection" / "frame_detection.csv").open(encoding="utf-8")))
    assert len(detection_rows) == 8
    assert sum(int(row["has_subtitle"]) for row in detection_rows) == 5
    result_json = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result_json["summary"]["groups"] == 2 and result_json["ocr_engine"] == "fake"
    assert (output / "debug" / "detection_visualization.jpg").is_file()
    assert (output / "debug" / "groups_visualization.jpg").is_file()
    assert (output / "ocr" / "group_0001.json").is_file()
    assert (output / "groups" / "group_0001" / "frame_000000.png").is_file()
    assert read_json(output / "video_info.json")["extraction"]["groups"] == 2
    assert read_json(output / "task_status.json")["status"] == "completed"


def test_crops_reuse_reruns_stages_two_to_five_without_decoding(input_path, tmp_path, install_decoder):
    install_decoder(["AAAAA", "AAAAA", "AAAAA", None, "BBBBB", "BBBBB"])
    first = tmp_path / "first"
    analyze_video(
        input_path,
        output_dir=first,
        save_frames=False,
        crop_config=CROP_CONFIG,
        extraction_config=ExtractionConfig(min_cue_duration=0.05),
        ocr_engine=FakeEngine([OcrReading("AAAAA", 0.99, "fake", 1)] * 3 + [OcrReading("BBBBB", 0.98, "fake", 1)] * 2),
    )

    reused = tmp_path / "reused"
    engine = FakeEngine([OcrReading("AAAAA", 0.99, "fake", 1)] * 3 + [OcrReading("BBBBB", 0.98, "fake", 1)] * 2)
    analyze_crops(
        first,
        output_dir=reused,
        extraction_config=ExtractionConfig(confidence_threshold=0.9, min_cue_duration=0.05),
        ocr_engine=engine,
    )

    srt = (reused / "result.srt").read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,000 --> 00:00:00,120\nAAAAA\n")
    assert "BBBBB" in srt
    status = read_json(reused / "task_status.json")
    assert status["status"] == "completed" and status["decoded_frames"] == 6
    assert read_json(reused / "video_info.json")["crops_source"] == str(first.resolve())
    detection_rows = list(csv.DictReader((reused / "detection" / "frame_detection.csv").open(encoding="utf-8")))
    assert len(detection_rows) == 6


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_low_confidence_triggers_fallback_frame(input_path, tmp_path, install_decoder):
    install_decoder(["AAAAA"] * 4)
    output = tmp_path / "task"
    engine = FakeEngine(
        [OcrReading("AAA", 0.4, "fake", 1), OcrReading("AAAAA", 0.95, "fake", 1), OcrReading("AAAAA", 0.95, "fake", 1)]
    )
    analyze_video(
        input_path,
        output_dir=output,
        save_frames=False,
        crop_config=CROP_CONFIG,
        extraction_config=ExtractionConfig(confidence_threshold=0.9, fallback_frames=2),
        ocr_engine=engine,
    )
    assert len(engine.calls) == 3
    group = json.loads((output / "groups" / "groups.json").read_text(encoding="utf-8"))[0]
    assert group["status"] == "ok" and group["text"] == "AAAAA"


def test_ocr_error_is_recorded_without_failing_the_task(input_path, tmp_path, install_decoder):
    install_decoder(["AAAAA"] * 4)
    output = tmp_path / "task"
    engine = FakeEngine([RuntimeError("engine crashed")])
    analyze_video(
        input_path,
        output_dir=output,
        save_frames=False,
        crop_config=CROP_CONFIG,
        extraction_config=ExtractionConfig(fallback_frames=0),
        ocr_engine=engine,
    )
    groups = json.loads((output / "groups" / "groups.json").read_text(encoding="utf-8"))
    # Groups that OCR to nothing are not published at all.
    assert groups == []
    assert (output / "result.srt").read_text(encoding="utf-8") == ""
    assert read_json(output / "task_status.json")["status"] == "completed"
    assert read_json(output / "result.json")["summary"]["ocr_failures"] == 1


def test_extraction_requires_an_active_crop(input_path, tmp_path, install_decoder):
    with pytest.raises(VideoAnalysisError, match="active crop region"):
        analyze_video(input_path, output_dir=tmp_path / "task", extraction_config=ExtractionConfig())


def test_extraction_without_engine_publishes_no_groups(input_path, tmp_path, install_decoder):
    install_decoder(["AAAAA"] * 3)
    output = tmp_path / "task"
    analyze_video(
        input_path,
        output_dir=output,
        save_frames=False,
        crop_config=CROP_CONFIG,
        extraction_config=ExtractionConfig(),
        ocr_engine=None,
    )
    # No OCR means no text: nothing to publish, but the task still completes.
    assert json.loads((output / "groups" / "groups.json").read_text(encoding="utf-8")) == []
    assert (output / "result.srt").read_text(encoding="utf-8") == ""


def test_extraction_config_validation():
    with pytest.raises(ExtractionError, match="confidence"):
        ExtractionConfig(confidence_threshold=1.5)
    with pytest.raises(ExtractionError, match="fallback"):
        ExtractionConfig(fallback_frames=-1)
    with pytest.raises(ExtractionError, match="merge gap"):
        ExtractionConfig(merge_gap=-0.1)


def make_extracted(group_id, text, start_time, end_time, confidence=0.9, status="ok"):
    return ExtractedGroup(
        group_id=group_id,
        group=SubtitleGroup(start_frame=group_id, end_frame=group_id + 1, frame_count=2, missing_frames=0),
        text=text,
        confidence=confidence,
        engine="fake",
        status=status,
        folder=f"group_{group_id:04d}",
        ocr_calls=1,
        start_time=start_time,
        end_time=end_time,
    )


def test_caption_normalisation_and_noise_rejection():
    assert normalize_caption("你好，世界！") == normalize_caption("你好 世界")
    assert is_meaningful_text("不管什么时候看")
    assert is_meaningful_text("hello")
    assert not is_meaningful_text("((")
    assert not is_meaningful_text("…。")


def test_identical_captions_merge_across_group_breaks(tmp_path):
    extractor = SubtitleExtractor(
        tmp_path,
        ExtractionConfig(),
        frame_width=FRAME_SIZE[0],
        frame_height=FRAME_SIZE[1],
        logger=logging.Logger("test"),
        engine=None,
        input_path=tmp_path / "episode.mp4",
    )
    extractor._groups = [
        make_extracted(1, "好可爱", 0.0, 0.5),
        make_extracted(2, "好可爱！", 0.55, 1.0),
        make_extracted(3, "走了", 3.0, 3.5),
    ]
    extractor._merge_groups()
    assert [item.text for item in extractor._groups] == ["好可爱", "走了"]
    assert extractor._groups[0].start_time == 0.0 and extractor._groups[0].end_time == 1.0
    assert extractor._merged_count == 1
    extractor._release()


def test_distant_or_different_captions_stay_separate(tmp_path):
    extractor = SubtitleExtractor(
        tmp_path,
        ExtractionConfig(),
        frame_width=FRAME_SIZE[0],
        frame_height=FRAME_SIZE[1],
        logger=logging.Logger("test"),
        engine=None,
        input_path=tmp_path / "episode.mp4",
    )
    extractor._groups = [
        make_extracted(1, "好可爱", 0.0, 0.5),
        make_extracted(2, "好可爱", 2.0, 2.5),
        make_extracted(3, "走了", 2.6, 3.0),
    ]
    extractor._merge_groups()
    assert len(extractor._groups) == 3 and extractor._merged_count == 0
    extractor._release()


def test_clean_caption_tidies_ocr_spacing_and_punctuation():
    assert clean_caption("  什么啊 不就是只鸟吗 ") == "什么啊不就是只鸟吗"
    assert clean_caption("啊.") == "啊"
    assert clean_caption("不需要100个铜锣烧...") == "不需要100个铜锣烧..."
    assert clean_caption("哆啦A梦 加油") == "哆啦A梦加油"
    assert caption_similarity("00光把直升机吸引过来了", "光把直升机吸引过来了") >= 0.8
    assert caption_similarity("等等", "等等我的点心") < 0.8


def test_junk_prefixed_variant_merges_into_the_clean_caption(tmp_path):
    extractor = SubtitleExtractor(
        tmp_path,
        ExtractionConfig(),
        frame_width=FRAME_SIZE[0],
        frame_height=FRAME_SIZE[1],
        logger=logging.Logger("test"),
        engine=None,
        input_path=tmp_path / "episode.mp4",
    )
    extractor._groups = [
        make_extracted(1, "光把直升机吸引过来了", 606.0, 606.3, confidence=1.0),
        make_extracted(2, "00光把直升机吸引过来了", 606.3, 606.4, confidence=0.93),
    ]
    extractor._merge_groups()
    assert len(extractor._groups) == 1
    group = extractor._groups[0]
    assert group.text == "光把直升机吸引过来了" and group.confidence == 1.0
    assert group.start_time == 606.0 and group.end_time == 606.4
    extractor._release()


def test_cues_shorter_than_the_floor_are_dropped(tmp_path):
    extractor = SubtitleExtractor(
        tmp_path,
        ExtractionConfig(),
        frame_width=FRAME_SIZE[0],
        frame_height=FRAME_SIZE[1],
        logger=logging.Logger("test"),
        engine=None,
        input_path=tmp_path / "episode.mp4",
    )
    # A one- or two-glyph line may flash for a couple of frames, so the floor
    # only guards longer lines.
    assert not extractor._is_srt_worthy(make_extracted(1, "好痛啊", 1.0, 1.1, confidence=0.99))
    assert extractor._is_srt_worthy(make_extracted(2, "好痛啊", 1.0, 1.4, confidence=0.99))
    extractor._release()


def test_weak_short_fragments_are_kept_out_of_the_srt(tmp_path):
    extractor = SubtitleExtractor(
        tmp_path,
        ExtractionConfig(),
        frame_width=FRAME_SIZE[0],
        frame_height=FRAME_SIZE[1],
        logger=logging.Logger("test"),
        engine=None,
        input_path=Path("episode.mp4"),
    )
    strong = make_extracted(1, "啊？", 0.0, 0.4, confidence=0.94)
    weak = make_extracted(2, "红", 1.0, 1.1, confidence=0.52, status="low_confidence")
    short = make_extracted(3, "om", 2.0, 2.07, confidence=0.69, status="low_confidence")
    decent = make_extracted(4, "谢谢惠顾", 3.0, 3.4, confidence=0.75, status="low_confidence")
    assert [extractor._is_srt_worthy(item) for item in (strong, weak, short, decent)] == [
        True,
        False,
        False,
        True,
    ]


def test_unreadable_ocr_text_is_not_written_to_srt(input_path, tmp_path, install_decoder):
    install_decoder(["AAAAA", "AAAAA"])
    output = tmp_path / "task"
    engine = FakeEngine([OcrReading("((", 0.99, "fake", 1)])
    analyze_video(
        input_path,
        output_dir=output,
        save_frames=False,
        crop_config=CROP_CONFIG,
        extraction_config=ExtractionConfig(),
        ocr_engine=engine,
    )
    groups = json.loads((output / "groups" / "groups.json").read_text(encoding="utf-8"))
    assert groups == []
    assert (output / "result.srt").read_text(encoding="utf-8") == ""


def test_cli_runs_ocr_and_reports_srt(tmp_path, monkeypatch):
    input_path = tmp_path / "episode.mp4"
    input_path.write_bytes(b"mock")
    calls = []
    engine_calls = []

    def fake_engine(name, language):
        engine_calls.append((name, language))
        return FakeEngine([])

    def fake_analyze(path, **kwargs):
        calls.append(kwargs)
        return AnalysisResult(
            tmp_path,
            {
                "frame_count": 8,
                "frame_rate_mode": "CFR",
                "anomaly_counts": {},
                "crop": {
                    "crop_count": 8,
                    "resolved_pixels": {"width": 432, "height": 68},
                    "output_width": 432,
                    "output_height": 68,
                    "variants": [],
                },
                "extraction": {
                    "groups": 2,
                    "groups_with_text": 2,
                    "detected_frames": 5,
                    "processed_frames": 8,
                    "ocr_calls": 2,
                },
            },
        )

    monkeypatch.setattr(cli, "create_ocr_engine", fake_engine)
    monkeypatch.setattr(cli, "analyze_video", fake_analyze)
    result = CliRunner().invoke(
        cli.app,
        ["--input", str(input_path), "--ocr-language", "ja", "--detection-threshold", "0.5"],
    )
    assert result.exit_code == 0, result.output
    assert engine_calls == [("auto", "ja")]
    extraction = calls[0]["extraction_config"]
    assert extraction is not None and extraction.detection.threshold == 0.5
    assert isinstance(calls[0]["ocr_engine"], FakeEngine)
    assert "Stages 2-5 completed: 2 groups" in result.output
    assert "result.srt" in result.output


@pytest.mark.parametrize(
    "args,message",
    [
        (["--detection-threshold", "0"], "within (0, 1]"),
        (["--similarity-threshold", "1.5"], "within (0, 1]"),
        (["--ocr-confidence-threshold", "2"], "within (0, 1]"),
        (["--max-missing-frames", "-1"], "negative"),
        (["--min-group-frames", "0"], "at least 1"),
        (["--ocr-language", " "], "cannot be empty"),
        (["--no-crop"], "needs an active crop region"),
    ],
)
def test_cli_rejects_invalid_extraction_arguments(tmp_path, args, message):
    input_path = tmp_path / "episode.mp4"
    input_path.write_bytes(b"mock")
    result = CliRunner().invoke(cli.app, ["--input", str(input_path), *args])
    assert result.exit_code == 2
    assert message.lower() in result.output.lower()


def test_cli_no_ocr_skips_extraction(tmp_path, monkeypatch):
    input_path = tmp_path / "episode.mp4"
    input_path.write_bytes(b"mock")
    calls = []

    def fake_analyze(path, **kwargs):
        calls.append(kwargs)
        return AnalysisResult(
            tmp_path,
            {
                "frame_count": 2,
                "frame_rate_mode": "CFR",
                "anomaly_counts": {},
            },
        )

    monkeypatch.setattr(cli, "analyze_video", fake_analyze)
    result = CliRunner().invoke(cli.app, ["--input", str(input_path), "--no-ocr"])
    assert result.exit_code == 0, result.output
    assert calls[0]["extraction_config"] is None
    assert calls[0]["ocr_engine"] is None
    assert "Stages 2-5" not in result.output
