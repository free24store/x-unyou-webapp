"""
DALL-E 3 画像生成サービス
OPENAI_API_KEY が設定されている場合に有効。
生成した画像はローカルの instance/images/ に保存する。
"""
import json
import os
import urllib.request
import urllib.error
from pathlib import Path


def generate_image(prompt: str, save_dir: str, filename: str):
    """
    DALL-E 3でプロンプトから画像を生成してローカルに保存する。

    Args:
        prompt: 英語の画像プロンプト
        save_dir: 保存先ディレクトリ（絶対パス）
        filename: 保存ファイル名（拡張子なし）

    Returns:
        保存したファイルの相対パス（instance/images/xxx.png）または None（失敗時）
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None

    body = json.dumps({
        "model": "dall-e-3",
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "response_format": "url",
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
            image_url = data["data"][0]["url"]

        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, f"{filename}.png")

        urllib.request.urlretrieve(image_url, save_path)
        return save_path

    except (urllib.error.URLError, KeyError, OSError):
        return None


def is_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", ""))
