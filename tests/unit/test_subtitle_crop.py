"""Exercise stage 1 cropping and preprocessing without a real video."""

import json
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from creator_agent.subtitle_extractor import analysis, cli
from creator_agent.subtitle_extractor.analysis import AnalysisResult, analyze_video
from creator_agent.subtitle_extractor.crop import (
    CropConfig,
    CropError,
    CropProcessor,
    CropRegion,
    PixelBox,
    parse_region,
    parse_variants,
)
from creator_agent.subtitle_extractor.timeline import VideoAnalysisError

FRAME_SIZE = (160, 90)


def make_frame(width: int = FRAME_SIZE[0], height: int = FRAME_SIZE[1]) -> Image.Image:
    """Dark frame with a bright subtitle-like bar so variants have real content."""
    image = Image.new("RGB", (width, height), (24, 24, 24))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, round(height * 0.8), width - 8, round(height * 0.93)), fill=(245, 245, 245))
    return image


def make_processor(tmp_path, config=None, *, size=FRAME_SIZE, name="task"):
    output = tmp_path / name
    return CropProcessor(
        output,
        config or CropConfig(region=CropRegion(5, 72, 95, 97)),
        frame_width=size[0],
        frame_height=size[1],
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
    )


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_percent_region_resolves_to_pixels_of_the_documented_default():
    box = CropRegion(5, 72, 95, 97).resolve(1920, 1080)
    assert box == PixelBox(left=96, top=778, right=1824, bottom=1048)
    assert (box.width, box.height) == (1728, 270)


def test_pixel_region_resolves_without_any_scaling():
    region = CropRegion(100, 200, 400, 300, unit="pixel")
    box = region.resolve(1920, 1080)
    assert box == PixelBox(100, 200, 400, 300)
    assert region.as_dict()["unit"] == "pixel"


def test_pixel_region_outside_the_frame_is_rejected():
    with pytest.raises(CropError, match="exceeds"):
        CropRegion(100, 200, 400, 300, unit="pixel").resolve(300, 300)
    with pytest.raises(CropError, match="negative"):
        CropRegion(-10, 0, 100, 100, unit="pixel").resolve(300, 300)


@pytest.mark.parametrize(
    "args",
    [
        (90, 0, 10, 50),
        (0, 50, 50, 50),
        (-1, 0, 50, 50),
        (0, 0, 101, 50),
        (0, 0, float("nan"), 50),
    ],
)
def test_invalid_percent_regions_are_rejected(args):
    with pytest.raises(CropError):
        CropRegion(*args)


def test_unknown_unit_and_tiny_region_are_rejected():
    with pytest.raises(CropError, match="Unknown crop unit"):
        CropRegion(0, 0, 10, 10, unit="ratio")
    with pytest.raises(CropError, match="too small"):
        CropRegion(0, 0, 1, 100, unit="pixel").resolve(300, 300)


def test_scale_up_changes_output_size_and_written_pixels(tmp_path):
    config = CropConfig(region=CropRegion(5, 72, 95, 97), scale=2.0, variants=())
    processor = make_processor(tmp_path, config)
    assert (processor.output_width, processor.output_height) == (288, 44)
    crop = processor.process(0, make_frame())
    assert (tmp_path / "task" / "crops" / "000000.png").is_file()
    assert crop.size == (288, 44)
    assert Image.open(tmp_path / "task" / "crops" / "000000.png").size == (288, 44)


@pytest.mark.parametrize("scale", [0.0, -1, 99, float("inf")])
def test_scale_out_of_range_is_rejected(scale):
    with pytest.raises(CropError, match="scale"):
        CropConfig(region=CropRegion(5, 72, 95, 97), scale=scale)


def test_variant_settings_and_names_are_validated():
    with pytest.raises(CropError, match="Unknown crop variants"):
        CropConfig(region=CropRegion(5, 72, 95, 97), variants=("gray", "edges"))
    with pytest.raises(CropError, match="odd"):
        CropConfig(region=CropRegion(5, 72, 95, 97), adaptive_block_size=30)
    with pytest.raises(CropError, match="clip limit"):
        CropConfig(region=CropRegion(5, 72, 95, 97), clahe_clip_limit=-1)


def test_all_variants_are_written_and_thresholds_are_binary(tmp_path):
    processor = make_processor(tmp_path)
    processor.process(7, make_frame())
    crops = tmp_path / "task" / "crops"
    assert (crops / "000007.png").is_file()
    for variant in ("gray", "clahe", "otsu", "adaptive"):
        assert (crops / variant / "000007.png").is_file()
    assert Image.open(crops / "gray" / "000007.png").mode == "L"
    for variant in ("otsu", "adaptive"):
        data = cv2.imread(str(crops / variant / "000007.png"), cv2.IMREAD_GRAYSCALE)
        assert set(np.unique(data)) <= {0, 255}
        assert data.max() == 255


def test_disabled_variants_write_color_crops_only(tmp_path):
    processor = make_processor(tmp_path, CropConfig(region=CropRegion(5, 72, 95, 97), variants=()))
    processor.process(0, make_frame())
    assert (tmp_path / "task" / "crops" / "000000.png").is_file()
    assert sorted(p.name for p in (tmp_path / "task" / "crops").iterdir() if p.is_dir()) == []


def test_every_frame_index_maps_to_one_crop_and_config_is_auditable(tmp_path):
    processor = make_processor(tmp_path)
    for index in range(5):
        processor.process(index, make_frame())
    crops = tmp_path / "task" / "crops"
    assert sorted(p.stem for p in crops.glob("*.png")) == [f"{index:06d}" for index in range(5)]
    processor.close("completed")
    config = read_json(tmp_path / "task" / "crop_config.json")
    assert config["stage"] == "stage1"
    assert config["status"] == "completed"
    assert config["crop_count"] == 5
    assert config["resolved_pixels"] == {"left": 8, "top": 65, "right": 152, "bottom": 87, "width": 144, "height": 22}
    assert config["output_width"] == 144 and config["output_height"] == 22
    assert config["path_pattern"] == "crops/{frame_index:06d}.png"
    assert config["frame_size_mismatch_count"] == 0
    assert config["variants"] == ["gray", "clahe", "otsu", "adaptive"]


def test_preview_is_a_readable_jpeg_with_sampled_frames(tmp_path):
    processor = make_processor(tmp_path)
    for index in range(12):
        processor.process(index, make_frame())
    processor.close("completed")
    preview = tmp_path / "task" / "crop_preview.jpg"
    assert preview.is_file()
    with Image.open(preview) as image:
        assert image.format == "JPEG"
        assert image.width > 0 and image.height > 0
    config = read_json(tmp_path / "task" / "crop_config.json")
    assert len(config["preview_samples"]) == 4
    assert config["preview_samples"] == sorted(config["preview_samples"])


def test_resolution_change_is_counted_and_cropped_per_frame(tmp_path):
    warnings = []
    processor = CropProcessor(
        tmp_path / "task",
        CropConfig(region=CropRegion(5, 72, 95, 97), variants=()),
        frame_width=160,
        frame_height=90,
        logger=SimpleNamespace(warning=lambda *args, **kwargs: warnings.append(args)),
    )
    processor.process(0, make_frame())
    processor.process(1, make_frame(320, 180))
    assert processor.frame_size_mismatch_count == 1
    assert warnings and warnings[0][1] == 1
    assert Image.open(tmp_path / "task" / "crops" / "000001.png").size == (288, 45)


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
        self.duration = 120000
        self.stream = SimpleNamespace(
            time_base=Fraction(1, 1000),
            average_rate=Fraction(25),
            start_time=0,
            duration=120,
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


@pytest.fixture
def input_path(tmp_path):
    path = tmp_path / "episode.mp4"
    path.write_bytes(b"not a real video; the decoder is mocked")
    return path


@pytest.fixture
def install_decoder(monkeypatch):
    def install(images=None):
        images = images if images is not None else [make_frame() for _ in range(3)]
        container = FakeContainer([FakeFrame(index * 40, image) for index, image in enumerate(images)])
        monkeypatch.setattr(
            analysis, "_load_av", lambda: SimpleNamespace(open=lambda path: container, time_base=1000000)
        )
        return container

    return install


def test_analysis_runs_stage_one_in_the_same_decode_pass(input_path, tmp_path, install_decoder):
    install_decoder()
    output = tmp_path / "task"
    result = analyze_video(
        input_path,
        output_dir=output,
        save_frames=True,
        crop_config=CropConfig(region=CropRegion(5, 72, 95, 97)),
    )
    crops = output / "crops"
    assert result.video_info["frame_count"] == 3
    assert result.video_info["crop"]["crop_count"] == 3
    assert sorted(p.stem for p in crops.glob("*.png")) == ["000000", "000001", "000002"]
    assert (output / "crops" / "otsu" / "000002.png").is_file()
    assert len(list((output / "frames").glob("*.png"))) == 3
    assert read_json(output / "crop_config.json")["status"] == "completed"
    assert read_json(output / "task_status.json")["stage"] == "stage0+stage1"
    assert read_json(output / "video_info.json")["crop"]["output_width"] == 144


def test_crop_failure_marks_the_task_failed_and_keeps_partial_data(input_path, tmp_path, install_decoder, monkeypatch):
    install_decoder()
    output = tmp_path / "task"

    def broken(self, frame_index, image):
        raise CropError("cannot crop frame 1")

    monkeypatch.setattr(CropProcessor, "process", broken)
    with pytest.raises(VideoAnalysisError, match="cannot crop frame 1"):
        analyze_video(input_path, output_dir=output, crop_config=CropConfig(region=CropRegion(5, 72, 95, 97)))
    assert read_json(output / "task_status.json")["status"] == "failed"
    assert read_json(output / "crop_config.json")["status"] == "failed"
    assert (output / "frame_index.csv").is_file()


def test_no_crop_keeps_stage_zero_outputs_only(input_path, tmp_path, install_decoder):
    install_decoder()
    output = tmp_path / "task"
    analyze_video(input_path, output_dir=output, save_frames=False)
    assert not (output / "crops").exists()
    assert not (output / "crop_config.json").exists()
    assert read_json(output / "task_status.json")["stage"] == "stage0"


def test_parse_region_and_variants():
    region = parse_region(" 5, 72 ,95,97 ", "percent")
    assert region == CropRegion(5, 72, 95, 97)
    assert parse_variants("gray, CLAHE ,gray") == ("gray", "clahe")
    assert parse_variants("none") == ()
    with pytest.raises(ValueError, match="four"):
        parse_region("5,72,95", "percent")
    with pytest.raises(ValueError, match="numbers"):
        parse_region("a,b,c,d", "percent")
    with pytest.raises(ValueError, match="Unknown"):
        parse_variants("gray,edge")


def test_cli_builds_crop_config_and_reports_stage_one(tmp_path, monkeypatch):
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
                "crop": {
                    "crop_count": 2,
                    "resolved_pixels": {"width": 144, "height": 22},
                    "output_width": 288,
                    "output_height": 44,
                    "variants": ["gray", "otsu"],
                },
            },
        )

    monkeypatch.setattr(cli, "analyze_video", fake_analyze)
    result = CliRunner().invoke(
        cli.app,
        [
            "--input",
            str(input_path),
            "--crop-box",
            "10,70,90,95",
            "--crop-scale",
            "2",
            "--crop-variants",
            "gray,otsu",
            "--no-save-frames",
            "--no-ocr",
        ],
    )
    assert result.exit_code == 0, result.output
    config = calls[0]["crop_config"]
    assert config.region == CropRegion(10, 70, 90, 95)
    assert config.scale == 2
    assert config.variants == ("gray", "otsu")
    assert "Stage 1 completed: 2 crops" in result.output
    assert "288x44" in result.output


@pytest.mark.parametrize(
    "args,message",
    [
        (["--crop-box", "5,72,95"], "four comma-separated"),
        (["--crop-box", "5,72,95,97", "--crop-unit", "ratio"], "Unknown crop unit"),
        (["--crop-variants", "gray,edges"], "Unknown"),
        (["--crop-scale", "0"], "between"),
        (["--crop-scale", "12"], "between"),
        (["--adaptive-block-size", "30"], "odd"),
    ],
)
def test_cli_rejects_invalid_crop_arguments(tmp_path, args, message):
    input_path = tmp_path / "episode.mp4"
    input_path.write_bytes(b"mock")
    result = CliRunner().invoke(cli.app, ["--input", str(input_path), *args])
    assert result.exit_code == 2
    assert message in result.output


def test_cli_help_lists_crop_options():
    result = CliRunner().invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for option in ("--crop/--no-crop", "--crop-box", "--crop-scale", "--crop-variants"):
        assert option.split("/")[0] in result.output


def test_cli_no_crop_disables_stage_one(tmp_path, monkeypatch):
    input_path = tmp_path / "episode.mp4"
    input_path.write_bytes(b"mock")
    calls = []

    def fake_analyze(path, **kwargs):
        calls.append(kwargs)
        return AnalysisResult(tmp_path, {"frame_count": 1, "frame_rate_mode": "CFR", "anomaly_counts": {}})

    monkeypatch.setattr(cli, "analyze_video", fake_analyze)
    result = CliRunner().invoke(cli.app, ["--input", str(input_path), "--no-crop", "--no-ocr"])
    assert result.exit_code == 0, result.output
    assert calls[0]["crop_config"] is None
    assert "Stage 1 completed" not in result.output
