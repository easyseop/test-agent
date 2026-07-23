"""시각 비교 로직 유닛 테스트."""
from PIL import Image

from webtest_agent.visual import compare_images


def _img(path, color, size=(100, 100)):
    Image.new("RGB", size, color).save(path)
    return path


def test_identical_images(tmp_path):
    a = _img(tmp_path / "a.png", (255, 255, 255))
    b = _img(tmp_path / "b.png", (255, 255, 255))
    ratio, note = compare_images(a, b, tmp_path / "d.png")
    assert ratio == 0.0 and note == ""
    assert not (tmp_path / "d.png").exists()


def test_partial_change_ratio_and_diff(tmp_path):
    a = _img(tmp_path / "a.png", (255, 255, 255))
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    img.paste((0, 0, 0), (0, 0, 10, 100))  # 10% 영역 변경
    img.save(tmp_path / "b.png")
    ratio, note = compare_images(a, tmp_path / "b.png", tmp_path / "d.png")
    assert abs(ratio - 0.10) < 0.001 and note == ""
    assert (tmp_path / "d.png").exists()


def test_tolerance_ignores_tiny_noise(tmp_path):
    a = _img(tmp_path / "a.png", (100, 100, 100))
    b = _img(tmp_path / "b.png", (105, 105, 105))  # 채널당 5 차이 → 허용 오차 이내
    ratio, _ = compare_images(a, b, tmp_path / "d.png")
    assert ratio == 0.0


def test_size_mismatch(tmp_path):
    a = _img(tmp_path / "a.png", (255, 255, 255), (100, 100))
    b = _img(tmp_path / "b.png", (255, 255, 255), (100, 120))
    ratio, note = compare_images(a, b, tmp_path / "d.png")
    assert ratio == 1.0 and "크기" in note
