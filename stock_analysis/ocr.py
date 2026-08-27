"""Optional local multi-pass OCR for brokerage quote screenshots."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import shutil


OCR_UNAVAILABLE_MESSAGE = "截图识别组件尚未安装，仍可使用手动输入或粘贴文字。"


@dataclass(frozen=True)
class OCRResult:
    text: str = ""
    confidence: float | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None


def _configure_tesseract(pytesseract: object) -> None:
    """Use PATH first, then standard Windows install locations."""
    if shutil.which("tesseract"):
        return
    for path in (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ):
        if path.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(path)
            return


def ocr_status() -> tuple[bool, str | None]:
    """Check the wrapper, executable, and required language packs."""
    try:
        import pytesseract

        _configure_tesseract(pytesseract)
        pytesseract.get_tesseract_version()
        languages = set(pytesseract.get_languages(config=""))
        if not {"chi_sim", "eng"}.issubset(languages):
            return False, "Tesseract 缺少 chi_sim 或 eng 语言包，仍可使用手动输入或粘贴文字。"
        return True, None
    except Exception:
        return False, OCR_UNAVAILABLE_MESSAGE


def _preprocessing_variants(image: object) -> dict[str, object]:
    """Create in-memory variants; no user image is written to disk."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    width, height = image.size
    upscaled = image.resize((width * 3, height * 3), Image.Resampling.LANCZOS)
    grayscale = ImageOps.grayscale(upscaled)
    contrast = ImageEnhance.Contrast(grayscale).enhance(2.0)
    sharpened = grayscale.filter(ImageFilter.UnsharpMask(radius=1.5, percent=180, threshold=2))
    threshold = contrast.point(lambda pixel: 255 if pixel >= 155 else 0, mode="1").convert("L")
    return {
        "original": image,
        "upscaled_3x": upscaled,
        "grayscale_contrast": contrast,
        "grayscale_sharpened": sharpened,
        "threshold": threshold,
    }


def _data_to_text(data: dict[str, list[object]]) -> tuple[str, list[float]]:
    """Rebuild lines so labels remain associated with adjacent values."""
    lines: list[str] = []
    confidences: list[float] = []
    current_key: tuple[object, ...] | None = None
    current_words: list[str] = []
    count = len(data.get("text", []))
    for index in range(count):
        key = tuple(data.get(name, [None] * count)[index] for name in ("page_num", "block_num", "par_num", "line_num"))
        word = str(data["text"][index]).strip()
        if current_key is not None and key != current_key and current_words:
            lines.append(" ".join(current_words))
            current_words = []
        current_key = key
        if not word:
            continue
        current_words.append(word)
        try:
            confidence = float(data.get("conf", [])[index])
        except (IndexError, TypeError, ValueError):
            continue
        if confidence >= 0:
            confidences.append(confidence)
    if current_words:
        lines.append(" ".join(current_words))
    return "\n".join(lines), confidences


def _merge_pass_texts(pass_texts: list[str]) -> str:
    """Deduplicate identical pass output without voting on financial values."""
    unique: list[str] = []
    seen: set[str] = set()
    for text in pass_texts:
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return "\n\n".join(unique)


def extract_screenshot_text(image_bytes: bytes) -> OCRResult:
    """Run local multi-pass OCR; bytes stay in memory and never leave the machine."""
    available, message = ocr_status()
    if not available:
        return OCRResult(error=message)
    try:
        from PIL import Image
        import pytesseract
        from pytesseract import Output

        _configure_tesseract(pytesseract)
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        variants = _preprocessing_variants(image)
        passes = (
            ("original", 11),
            ("upscaled_3x", 6),
            ("upscaled_3x", 11),
            ("grayscale_contrast", 6),
            ("grayscale_sharpened", 11),
            ("threshold", 6),
        )
        pass_texts: list[str] = []
        confidences: list[float] = []
        failed_passes = 0
        for variant_name, psm in passes:
            try:
                data = pytesseract.image_to_data(
                    variants[variant_name], lang="chi_sim+eng", config=f"--psm {psm}", output_type=Output.DICT
                )
                text, pass_confidences = _data_to_text(data)
                if text:
                    pass_texts.append(text)
                    confidences.extend(pass_confidences)
            except Exception:
                failed_passes += 1

        # Numeric output supplements the Chinese/English label passes. It is not
        # treated as independent confirmation or allowed to override ambiguity.
        try:
            numeric_text = pytesseract.image_to_string(
                variants["grayscale_contrast"],
                lang="eng",
                config="--psm 11 -c classify_bln_numeric_mode=1 -c tessedit_char_whitelist=0123456789.,+-%",
            ).strip()
            if numeric_text:
                pass_texts.append(numeric_text)
        except Exception:
            failed_passes += 1

        text = _merge_pass_texts(pass_texts)
        average_confidence = sum(confidences) / len(confidences) if confidences else None
        warnings: list[str] = []
        if failed_passes:
            warnings.append(f"部分 OCR 识别轮次失败（{failed_passes} 次），请重点人工核对结果。")
        if average_confidence is not None and average_confidence < 70:
            warnings.append("截图识别置信度较低，请重点核对所有数字、符号和单位。")
        if not text:
            warnings.append("截图未识别出可用文字，请改用手动输入或粘贴文字。")
        return OCRResult(text, average_confidence, tuple(warnings))
    except Exception:
        return OCRResult(error="截图识别失败，仍可使用手动输入或粘贴文字。")
