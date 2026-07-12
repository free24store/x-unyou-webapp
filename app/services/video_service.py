"""
ストーリー型投稿テキストからショート動画（縦型 9:16）を生成する。
PIL でテキストフレームを描画 → imageio で MP4 に合成。
外部ツール（ImageMagick / ffmpeg）不要で動作する。
"""
import os
import re
import textwrap
from pathlib import Path
from datetime import datetime

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import imageio
    IMAGEIO_AVAILABLE = True
except ImportError:
    IMAGEIO_AVAILABLE = False

# 動画仕様
WIDTH, HEIGHT = 1080, 1920   # 縦型 9:16
FPS = 30
BG_COLOR = (15, 15, 15)       # ほぼ黒
TEXT_COLOR = (255, 255, 255)
ACCENT_COLOR = (99, 102, 241) # インジゴ

# フォント（システムフォントを探す）
FONT_PATHS = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/Hiragino Sans GB W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode MS.ttf",
]

def _get_font(size: int):
    for p in FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(text: str, font, max_width: int, draw):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_slide(title: str, body: str, subtitle: str = "",
                bg_color=BG_COLOR, accent=ACCENT_COLOR,
                progress: float = 0.0) -> np.ndarray:
    """1枚のスライド画像 (numpy array) を生成する。"""
    img = Image.new("RGB", (WIDTH, HEIGHT), bg_color)
    draw = ImageDraw.Draw(img)

    margin = 80
    inner_w = WIDTH - margin * 2

    # アクセントバー（上部）
    draw.rectangle([(0, 0), (WIDTH, 8)], fill=accent)

    # プログレスバー（上部）
    if progress > 0:
        draw.rectangle([(0, 8), (int(WIDTH * progress), 14)], fill=accent)

    # サブタイトル（小さいラベル）
    if subtitle:
        sub_font = _get_font(36)
        draw.text((margin, 80), subtitle, font=sub_font, fill=accent)

    # タイトル
    title_font = _get_font(72)
    y = 160 if subtitle else 120
    for line in textwrap.wrap(title, width=18):
        draw.text((margin, y), line, font=title_font, fill=TEXT_COLOR)
        bbox = draw.textbbox((0, 0), line, font=title_font)
        y += (bbox[3] - bbox[1]) + 16

    # 区切り線
    y += 30
    draw.rectangle([(margin, y), (WIDTH - margin, y + 3)], fill=accent)
    y += 40

    # 本文
    body_font = _get_font(52)
    for para in body.split("\n"):
        for line in textwrap.wrap(para, width=22):
            draw.text((margin, y), line, font=body_font, fill=(220, 220, 220))
            bbox = draw.textbbox((0, 0), line, font=body_font)
            y += (bbox[3] - bbox[1]) + 12
        y += 20

    # アクセントバー（下部）
    draw.rectangle([(0, HEIGHT - 8), (WIDTH, HEIGHT)], fill=accent)

    return np.array(img)


def generate_story_video(story: dict, profile: dict, output_dir: str) -> str:
    """
    起承転結ストーリーから縦型ショート動画（MP4）を生成する。

    story: {ki, sho, ten, ketsu}
    profile: {genre, display_name, ...}
    output_dir: 保存先ディレクトリ
    Returns: 生成されたMP4ファイルの絶対パス
    """
    if not PIL_AVAILABLE or not IMAGEIO_AVAILABLE:
        raise RuntimeError("動画生成ライブラリ（numpy/Pillow/imageio）がインストールされていません。")

    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(output_dir, f"story_{ts}.mp4")

    genre = profile.get("genre", "")
    name = profile.get("display_name", "") or profile.get("position", "")

    # スライド定義: (タイトル, 本文, サブラベル, 秒数, プログレス)
    slides = [
        ("Hook", story.get("ki", ""), f"#{genre} {name}", 4, 0.0),
        ("展開", story.get("sho", ""), "ストーリー②", 5, 0.33),
        ("転機・事件", story.get("ten", ""), "ストーリー③", 5, 0.66),
        ("結末・現在地", story.get("ketsu", ""), "ストーリー④", 4, 0.9),
        ("フォローして続きを見る", f"@{name}\n#{genre}", "👆 フォロー必須", 3, 1.0),
    ]

    writer = imageio.get_writer(out_path, fps=FPS, codec="libx264",
                                quality=7, macro_block_size=None)
    try:
        for title, body, subtitle, duration_sec, progress in slides:
            frame = _draw_slide(title, body, subtitle, progress=progress)
            for _ in range(FPS * duration_sec):
                writer.append_data(frame)
    finally:
        writer.close()

    return out_path


def generate_draft_video(draft_text: str, profile: dict, output_dir: str) -> str:
    """
    通常ドラフトテキストからショート動画を生成する。
    テキストを Hook / 本編 / CTA の3パートに自動分割。
    """
    if not PIL_AVAILABLE or not IMAGEIO_AVAILABLE:
        raise RuntimeError("動画生成ライブラリ（numpy/Pillow/imageio）がインストールされていません。")

    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(output_dir, f"draft_{ts}.mp4")

    lines = [l.strip() for l in draft_text.strip().split("\n") if l.strip()]
    hook = lines[0] if lines else ""
    main = "\n".join(lines[1:-1]) if len(lines) > 2 else "\n".join(lines[1:])
    cta = lines[-1] if len(lines) > 1 else "フォローして見逃さないでください"

    genre = profile.get("genre", "")
    name = profile.get("display_name", "") or profile.get("position", "")

    slides = [
        (hook, "", f"#{genre}", 4, 0.0),
        ("詳しくはこちら", main, "本編", 8, 0.5),
        ("CTA", cta, f"@{name}", 3, 1.0),
    ]

    writer = imageio.get_writer(out_path, fps=FPS, codec="libx264",
                                quality=7, macro_block_size=None)
    try:
        for title, body, subtitle, duration_sec, progress in slides:
            frame = _draw_slide(title, body, subtitle, progress=progress)
            for _ in range(FPS * duration_sec):
                writer.append_data(frame)
    finally:
        writer.close()

    return out_path
