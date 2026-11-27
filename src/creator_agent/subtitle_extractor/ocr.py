"""Stage 4: read Japanese (or configured) text from the selected subtitle frame.

The engine is an interface, so the pipeline never depends on one OCR package.
PaddleOCR is the first implementation; new engines only need to provide
``name`` and ``read(image) -> OcrReading``.
"""

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np
from PIL import Image

from creator_agent.subtitle_extractor.timeline import VideoAnalysisError

LANGUAGE_ALIASES = {
    "ja": "japan",
    "jp": "japan",
    "japanese": "japan",
    "zh": "ch",
    "chs": "ch",
    "chinese": "ch",
    "en": "en",
    "english": "en",
    "ko": "korean",
    "korean": "korean",
}
SUPPORTED_ENGINES = ("paddleocr",)


class OcrError(VideoAnalysisError):
    """The OCR engine is missing, misconfigured, or failed."""


@dataclass(frozen=True)
class OcrReading:
    text: str
    confidence: float
    engine: str
    box_count: int
    status: str = "ok"

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "engine": self.engine,
            "box_count": self.box_count,
            "status": self.status,
        }


class OcrEngine(Protocol):
    name: str

    def read(self, image: Image.Image) -> OcrReading: ...


def normalize_language(language: str) -> str:
    key = language.strip().lower()
    if not key:
        raise OcrError("OCR language cannot be empty")
    return LANGUAGE_ALIASES.get(key, key)


def _first_value(result: object, key: str) -> object | None:
    if isinstance(result, dict):
        return result.get(key)
    try:
        return result[key]
    except (KeyError, TypeError, IndexError):
        pass
    payload = getattr(result, "json", None)
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        nested = payload.get("res")
        if isinstance(nested, dict):
            return nested.get(key)
    return None


def _parse_pipeline_results(results: object, engine_name: str) -> OcrReading:
    items = list(results) if results is not None else []
    if not items:
        return OcrReading("", 0.0, engine_name, 0, "empty")
    texts = _first_value(items[0], "rec_texts") or []
    scores = _first_value(items[0], "rec_scores") or []
    cleaned: list[str] = []
    kept_scores: list[float] = []
    for text, score in zip(texts, scores, strict=False):
        text = str(text).strip()
        if not text:
            continue
        cleaned.append(text)
        kept_scores.append(float(score))
    if not cleaned:
        return OcrReading("", 0.0, engine_name, 0, "empty")
    return OcrReading(
        text="".join(cleaned),
        confidence=sum(kept_scores) / len(kept_scores),
        engine=engine_name,
        box_count=len(cleaned),
    )


def _parse_legacy_results(results: object, engine_name: str) -> OcrReading:
    """Parse the classic ``PaddleOCR.ocr`` list-of-lines format."""
    if not results:
        return OcrReading("", 0.0, engine_name, 0, "empty")
    lines = results[0] if isinstance(results, (list, tuple)) and len(results) == 1 else results
    cleaned: list[str] = []
    kept_scores: list[float] = []
    for line in lines or []:
        try:
            _, (text, score) = line
        except (TypeError, ValueError):
            continue
        text = str(text).strip()
        if not text:
            continue
        cleaned.append(text)
        kept_scores.append(float(score))
    if not cleaned:
        return OcrReading("", 0.0, engine_name, 0, "empty")
    return OcrReading(
        text="".join(cleaned),
        confidence=sum(kept_scores) / len(kept_scores),
        engine=engine_name,
        box_count=len(cleaned),
    )


class PaddleOcrEngine:
    name = "paddleocr"

    def __init__(self, language: str = "japan") -> None:
        self.language = normalize_language(language)
        try:
            import paddle
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OcrError(
                "PaddleOCR is not installed. Run: uv sync --extra subtitle-ocr (or use --no-ocr to skip recognition)."
            ) from exc
        # PaddlePaddle 3.x crashes on some CPUs when oneDNN optimisations are
        # enabled; disabling them keeps CPU inference stable at little cost.
        try:
            paddle.set_flags({"FLAGS_use_mkldnn": False})
        except Exception:  # pragma: no cover - flag support varies by build
            pass
        options = dict(
            lang=self.language,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
        )
        try:
            self._ocr = PaddleOCR(**options)
        except TypeError:
            self._ocr = PaddleOCR(lang=self.language)

    def read(self, image: Image.Image) -> OcrReading:
        rgb = np.asarray(image.convert("RGB"))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if hasattr(self._ocr, "predict"):
            return _parse_pipeline_results(self._ocr.predict(bgr), self.name)
        return _parse_legacy_results(self._ocr.ocr(bgr, cls=False), self.name)


def create_ocr_engine(name: str, language: str) -> OcrEngine | None:
    key = name.strip().lower()
    if key in {"none", "off", "disabled"}:
        return None
    if key in {"auto", "paddleocr"}:
        return PaddleOcrEngine(language)
    raise OcrError(f"Unknown OCR engine: {name}. Supported: auto, none, {', '.join(SUPPORTED_ENGINES)}")


def is_confident(reading: OcrReading, threshold: float) -> bool:
    if not reading.text or reading.status != "ok":
        return False
    return reading.confidence >= threshold
