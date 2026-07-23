"""시각 회귀(기준선 대조) — 픽셀 비교와 diff 이미지 생성."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

PIXEL_TOLERANCE = 12  # 채널당 이 값 이하의 미세한 렌더링 차이는 무시


def compare_images(baseline: Path, current: Path, diff_out: Path) -> tuple[float, str]:
    """달라진 픽셀 비율(0.0~1.0)과 비고를 반환. diff 이미지는 변경 픽셀을 마젠타로 표시."""
    img_a = Image.open(baseline).convert("RGB")
    img_b = Image.open(current).convert("RGB")
    if img_a.size != img_b.size:
        return 1.0, f"이미지 크기가 다릅니다 (기준선 {img_a.size} vs 현재 {img_b.size})"

    gray = ImageChops.difference(img_a, img_b).convert("L")
    hist = gray.histogram()
    total = img_a.size[0] * img_a.size[1]
    changed = sum(hist[PIXEL_TOLERANCE + 1:])
    ratio = changed / total if total else 0.0

    if changed:
        mask = gray.point(lambda p: 255 if p > PIXEL_TOLERANCE else 0)
        diff_img = img_b.copy()
        diff_img.paste((255, 0, 255), mask=mask)
        diff_out.parent.mkdir(parents=True, exist_ok=True)
        diff_img.save(diff_out)
    return ratio, ""
