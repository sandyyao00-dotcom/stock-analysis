"""Regression tests for local screenshot OCR and its safe fallback."""

from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

from stock_analysis.ocr import (
    OCR_UNAVAILABLE_MESSAGE,
    _merge_pass_texts,
    _preprocessing_variants,
    extract_screenshot_text,
    ocr_status,
)


class OCRPipelineTests(unittest.TestCase):
    def test_preprocessing_variants_are_created_in_memory(self):
        from PIL import Image

        image = Image.new("RGB", (100, 60), "white")
        variants = _preprocessing_variants(image)
        self.assertEqual(
            set(variants),
            {"original", "upscaled_3x", "grayscale_contrast", "grayscale_sharpened", "threshold"},
        )
        self.assertEqual(variants["upscaled_3x"].size, (300, 180))

    def test_merge_deduplicates_passes_without_voting(self):
        merged = _merge_pass_texts(["现价 10.00", "现价 10.00", "现价 10.10"])
        self.assertEqual(merged.count("现价 10.00"), 1)
        self.assertIn("现价 10.10", merged)

    @patch("stock_analysis.ocr.ocr_status", return_value=(False, OCR_UNAVAILABLE_MESSAGE))
    def test_ocr_unavailable_fallback(self, _mock_status):
        result = extract_screenshot_text(b"not-an-image")
        self.assertEqual(result.error, OCR_UNAVAILABLE_MESSAGE)

    def test_deterministic_synthetic_quote(self):
        available, _ = ocr_status()
        font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
        if not available or not font_path.exists():
            self.skipTest("本机 OCR 或中文测试字体不可用")

        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (900, 620), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(font_path), 42)
        lines = (
            "600519 贵州茅台",
            "现价 1293.50",
            "涨幅 +1.25%",
            "今开 1281.00",
            "最高 1298.80",
            "最低 1278.20",
            "昨收 1277.50",
            "成交额 38.2亿",
            "换手率 0.31%",
        )
        for index, line in enumerate(lines):
            draw.text((40, 25 + index * 62), line, fill="black", font=font)
        buffer = BytesIO()
        image.save(buffer, format="PNG")

        result = extract_screenshot_text(buffer.getvalue())
        self.assertIsNone(result.error)
        self.assertIn("600519", result.text)
        self.assertIn("1293.50", result.text)


if __name__ == "__main__":
    unittest.main()
