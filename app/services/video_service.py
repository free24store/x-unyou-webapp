"""
ショート動画（縦型 9:16）生成サービス — 経路プラガブル版（E2-5）。

動画生成を mode でプラガブルにディスパッチする：
  mode="client" … サーバ生成せず、ブラウザ動画スタジオ用のスライド構成JSONを返す
                  （キー不要の既定・Render負荷ゼロ・テンプレファースト）
  mode="local"  … 既存の Pillow/imageio 実装に委譲（開発機のみ想定・重ライブラリは遅延import）
  mode="api"    … 外部動画API（VIDEO_API_KEY があれば有効化の枠。実接続は未実装）
  mode="studio" … NotebookLM Studio 経路の枠（IFのみ。実生成はしない）

統一IF: generate_video(story, profile, mode=None, output_dir=None) -> dict
既定は default_mode()="client"。env と可用性で自動選択。

※ numpy/Pillow/imageio は Render 軽量化のため関数内で遅延import する。
  未インストールでもこのモジュールの import／アプリ起動は壊れない。
"""
import os
import textwrap
from datetime import datetime

# 動画仕様
WIDTH, HEIGHT = 1080, 1920   # 縦型 9:16
FPS = 30
BG_COLOR = (15, 15, 15)       # ほぼ黒
TEXT_COLOR = (255, 255, 255)
ACCENT_COLOR = (99, 102, 241)  # インジゴ

# フォント（システムフォントを探す）
FONT_PATHS = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/Hiragino Sans GB W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode MS.ttf",
]


# ──────────── 遅延import / 可用性 ────────────

def _load_libs():
    """
    重ライブラリ（numpy/Pillow/imageio）を遅延importする。
    未インストールなら None を返す（起動・importを壊さない）。
    """
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        import imageio
        return {
            "np": np,
            "Image": Image,
            "ImageDraw": ImageDraw,
            "ImageFont": ImageFont,
            "imageio": imageio,
        }
    except ImportError:
        return None


def _local_available() -> bool:
    """local 経路（Pillow/imageio）が使えるか。実importせず spec だけ確認。"""
    from importlib.util import find_spec
    for mod in ("numpy", "PIL", "imageio"):
        try:
            if find_spec(mod) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def is_available(mode: str) -> bool:
    """指定 mode が現環境で利用可能か。"""
    if mode == "client":
        return True  # テンプレファースト：常に利用可
    if mode == "local":
        return _local_available()
    if mode == "api":
        return bool(os.environ.get("VIDEO_API_KEY", ""))
    if mode == "studio":
        # NotebookLM Studio 経路の枠。env が無ければ False。
        return bool(
            os.environ.get("VIDEO_STUDIO_ENABLED", "")
            or os.environ.get("NOTEBOOKLM_AUTH", "")
        )
    return False


def default_mode() -> str:
    """既定 mode。Render でゼロコストな client を既定にする。"""
    return "client"


def _auto_mode() -> str:
    """
    mode 未指定時の自動選択。env と可用性で決める。
    重い local を既定にはしない（外部枠が env で有効な時のみ選択、無ければ client）。
    """
    if is_available("api"):
        return "api"
    if is_available("studio"):
        return "studio"
    return "client"


# ──────────── スライド構成（client / local 共通） ────────────

def _build_slides(story: dict, profile: dict) -> list:
    """
    起承転結ストーリーからスライド構成（描画非依存のデータ）を組み立てる。
    client 経路の JSON と local 経路の描画で共通利用する。

    Returns: list of dict
      {title, body, subtitle, duration_sec, progress, bg_color, accent_color}
    """
    genre = profile.get("genre", "") if profile else ""
    name = ""
    if profile:
        name = profile.get("display_name", "") or profile.get("position", "")

    raw = [
        ("Hook", story.get("ki", ""), f"#{genre} {name}".strip(), 4, 0.0),
        ("展開", story.get("sho", ""), "ストーリー②", 5, 0.33),
        ("転機・事件", story.get("ten", ""), "ストーリー③", 5, 0.66),
        ("結末・現在地", story.get("ketsu", ""), "ストーリー④", 4, 0.9),
        ("フォローして続きを見る", f"@{name}\n#{genre}", "👆 フォロー必須", 3, 1.0),
    ]
    slides = []
    for title, body, subtitle, duration_sec, progress in raw:
        slides.append({
            "title": title,
            "body": body,
            "subtitle": subtitle,
            "duration_sec": duration_sec,
            "progress": progress,
            "bg_color": list(BG_COLOR),
            "accent_color": list(ACCENT_COLOR),
        })
    return slides


# ──────────── 経路ディスパッチ ────────────

def generate_video(story: dict, profile: dict, mode: str = None,
                   output_dir: str = None) -> dict:
    """
    統一IF。story/profile から mode に応じた経路で動画（素材）を生成する。

    Args:
        story:   {ki, sho, ten, ketsu}
        profile: {genre, display_name, position, ...}
        mode:    "client" | "local" | "api" | "studio"（None なら自動選択）
        output_dir: local 経路の保存先（省略時は生成しない）

    Returns:
        dict — 少なくとも {mode, status, ...} を含む。
        status は "ready"（利用可能な結果あり）／"unsupported"（当環境で不可）。
    """
    profile = profile or {}
    story = story or {}
    if mode is None:
        mode = _auto_mode()

    if mode == "client":
        return _generate_client(story, profile)
    if mode == "local":
        return _generate_local(story, profile, output_dir)
    if mode == "api":
        return _generate_api(story, profile)
    if mode == "studio":
        return _generate_studio(story, profile)

    return {"mode": mode, "status": "unsupported",
            "reason": f"未知の mode: {mode}"}


def _generate_client(story: dict, profile: dict) -> dict:
    """案C：ブラウザ動画スタジオ用のスライド構成JSONを返す（キー不要の既定）。"""
    return {
        "mode": "client",
        "status": "ready",
        "renderer": "browser",   # 実描画はブラウザ（Canvas/MediaRecorder等）
        "format": "slides",
        "spec": {
            "width": WIDTH,
            "height": HEIGHT,
            "fps": FPS,
            "aspect": "9:16",
        },
        "slides": _build_slides(story, profile),
    }


def _generate_local(story: dict, profile: dict, output_dir: str) -> dict:
    """案D（開発機のみ）：既存 Pillow/imageio 実装に委譲。未対応なら壊さず返す。"""
    if not _local_available():
        return {
            "mode": "local",
            "status": "unsupported",
            "reason": "動画生成ライブラリ（numpy/Pillow/imageio）が未インストールです。",
        }
    if not output_dir:
        return {
            "mode": "local",
            "status": "unsupported",
            "reason": "output_dir が指定されていません。",
        }
    try:
        path = generate_story_video(story, profile, output_dir)
    except RuntimeError as e:
        return {"mode": "local", "status": "unsupported", "reason": str(e)}
    return {"mode": "local", "status": "ready", "format": "mp4", "path": path}


def _generate_api(story: dict, profile: dict) -> dict:
    """案B：外部動画API の枠。VIDEO_API_KEY が無ければ None を返すスタブ。"""
    if not is_available("api"):
        return None
    # 実API接続は今回は実装しない（env が無いので実際には走らない枠）。
    return {
        "mode": "api",
        "status": "unsupported",
        "reason": "外部動画API連携は未実装（枠のみ）。client 経路にフォールバックしてください。",
    }


def _generate_studio(story: dict, profile: dict) -> dict:
    """案A：NotebookLM Studio 経路の枠。IF のみ・実生成はしない。"""
    if not is_available("studio"):
        return None
    return {
        "mode": "studio",
        "status": "unsupported",
        "reason": "Studio 経路は未実装（枠のみ）。非同期ジョブ実装は E2-2 で対応。",
    }


# ──────────── local 描画実装（遅延import・開発機のみ） ────────────

def _get_font(size: int, ImageFont):
    for p in FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_slide(libs, title: str, body: str, subtitle: str = "",
                bg_color=BG_COLOR, accent=ACCENT_COLOR,
                progress: float = 0.0):
    """1枚のスライド画像 (numpy array) を生成する。"""
    np = libs["np"]
    Image = libs["Image"]
    ImageDraw = libs["ImageDraw"]
    ImageFont = libs["ImageFont"]

    img = Image.new("RGB", (WIDTH, HEIGHT), bg_color)
    draw = ImageDraw.Draw(img)

    margin = 80

    # アクセントバー（上部）
    draw.rectangle([(0, 0), (WIDTH, 8)], fill=accent)

    # プログレスバー（上部）
    if progress > 0:
        draw.rectangle([(0, 8), (int(WIDTH * progress), 14)], fill=accent)

    # サブタイトル（小さいラベル）
    if subtitle:
        sub_font = _get_font(36, ImageFont)
        draw.text((margin, 80), subtitle, font=sub_font, fill=accent)

    # タイトル
    title_font = _get_font(72, ImageFont)
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
    body_font = _get_font(52, ImageFont)
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
    起承転結ストーリーから縦型ショート動画（MP4）を生成する（local 実装・開発機のみ）。

    story: {ki, sho, ten, ketsu}
    profile: {genre, display_name, ...}
    output_dir: 保存先ディレクトリ
    Returns: 生成されたMP4ファイルの絶対パス
    """
    libs = _load_libs()
    if libs is None:
        raise RuntimeError("動画生成ライブラリ（numpy/Pillow/imageio）がインストールされていません。")
    imageio = libs["imageio"]

    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(output_dir, f"story_{ts}.mp4")

    slides = _build_slides(story, profile)

    writer = imageio.get_writer(out_path, fps=FPS, codec="libx264",
                                quality=7, macro_block_size=None)
    try:
        for s in slides:
            frame = _draw_slide(libs, s["title"], s["body"], s["subtitle"],
                                progress=s["progress"])
            for _ in range(FPS * s["duration_sec"]):
                writer.append_data(frame)
    finally:
        writer.close()

    return out_path


def generate_draft_video(draft_text: str, profile: dict, output_dir: str) -> str:
    """
    通常ドラフトテキストからショート動画を生成する（local 実装・開発機のみ）。
    テキストを Hook / 本編 / CTA の3パートに自動分割。
    """
    libs = _load_libs()
    if libs is None:
        raise RuntimeError("動画生成ライブラリ（numpy/Pillow/imageio）がインストールされていません。")
    imageio = libs["imageio"]

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
            frame = _draw_slide(libs, title, body, subtitle, progress=progress)
            for _ in range(FPS * duration_sec):
                writer.append_data(frame)
    finally:
        writer.close()

    return out_path
