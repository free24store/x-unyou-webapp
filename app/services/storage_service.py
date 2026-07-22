"""
生成物（画像/動画）永続ストレージサービス — プロバイダ・プラガブル版（E1-3）。

Render 無料/標準 Web の disk は ephemeral（再デプロイ・再起動・スリープ復帰で消える）。
instance/ に書いた生成物は失われるため、env が設定されている場合のみ
S3 互換オブジェクトストレージ（Cloudflare R2 / Backblaze B2 / AWS S3 / MinIO）へ退避する。

テンプレートファースト:
  env 未設定なら **従来どおりローカル instance/ 保存**（＝揮発キャッシュ）にフォールバックする。
  キー無しでも import・起動・保存はすべて壊れない。

プロバイダ:
  STORAGE_PROVIDER=local … 既定。ローカル instance/ 保存。
  STORAGE_PROVIDER=s3    … S3 互換（R2/B2/S3/MinIO）。env が揃っていれば自動選択。
  STORAGE_PROVIDER=cloudinary … Cloudinary（CLOUDINARY_URL がある場合の任意経路）。
  未指定なら env の有無から自動判定（cloudinary → s3 → local の順）。

統一IF:
  is_available() -> bool      … 永続化プロバイダ（非 local）の env が揃っているか
  is_persistent() -> bool     … is_available() の別名（意図を明示する用途）
  active_provider() -> str     … 実際に使われるプロバイダ名（"local"/"s3"/"cloudinary"）
  save(source, key, content_type=None) -> str
        source: bytes もしくはローカルファイルパス(str/Path)
        戻り値: 永続化時は公開URL、ローカル時は保存先の絶対パス（従来動作）

※ boto3 / cloudinary は **関数内で遅延import**（E1-1 の重ライブラリ方針）。
  未インストールでもこのモジュールの import／アプリ起動は壊れない。
  アップロードに失敗した場合も例外を投げず、ローカル保存にフォールバックする（全断回避）。
"""
import logging
import mimetypes
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ローカル保存の既定サブディレクトリ（instance/ 配下）
LOCAL_ROOT_SUBDIR = ""  # key に "images/xxx.png" 等を含める前提

# リポジトリ直下の instance/（app コンテキスト外でのフォールバック用）
_FALLBACK_INSTANCE = Path(__file__).resolve().parent.parent.parent / "instance"


# ---------------------------------------------------------------------------
# env 読み取り（エイリアス許容: S3_* を正、AWS_*/R2_* も受ける）
# ---------------------------------------------------------------------------
def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def _s3_config() -> dict:
    return {
        "bucket": _env("S3_BUCKET", "R2_BUCKET"),
        "access_key": _env("S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID", "R2_ACCESS_KEY_ID"),
        "secret_key": _env("S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY", "R2_SECRET_ACCESS_KEY"),
        "endpoint_url": _env("S3_ENDPOINT_URL", "R2_ENDPOINT_URL"),
        "public_base_url": _env("S3_PUBLIC_BASE_URL", "R2_PUBLIC_BASE_URL"),
        "region": _env("S3_REGION", "AWS_DEFAULT_REGION", default="auto"),
    }


def _s3_env_ready() -> bool:
    c = _s3_config()
    return bool(c["bucket"] and c["access_key"] and c["secret_key"])


def _cloudinary_env_ready() -> bool:
    return bool(_env("CLOUDINARY_URL"))


# ---------------------------------------------------------------------------
# プロバイダ解決
# ---------------------------------------------------------------------------
def active_provider() -> str:
    """実際に使用するプロバイダ名を返す。env が揃っていなければ 'local'。"""
    explicit = (os.environ.get("STORAGE_PROVIDER") or "").strip().lower()
    if explicit == "local":
        return "local"
    if explicit == "s3":
        return "s3" if _s3_env_ready() else "local"
    if explicit == "cloudinary":
        return "cloudinary" if _cloudinary_env_ready() else "local"
    # 未指定 → 自動判定
    if _cloudinary_env_ready():
        return "cloudinary"
    if _s3_env_ready():
        return "s3"
    return "local"


def is_available() -> bool:
    """永続化（非 local）プロバイダの env が揃っているか。"""
    return active_provider() != "local"


def is_persistent() -> bool:
    """is_available() の別名（呼び出し側の意図を明示するため）。"""
    return is_available()


# ---------------------------------------------------------------------------
# 保存本体
# ---------------------------------------------------------------------------
def _as_bytes(source) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    # パス（str / Path）とみなして読む
    with open(source, "rb") as f:
        return f.read()


def _guess_content_type(key: str, content_type: str = None) -> str:
    if content_type:
        return content_type
    guessed, _ = mimetypes.guess_type(key)
    return guessed or "application/octet-stream"


def _instance_dir() -> Path:
    """app コンテキストがあれば instance_path、無ければリポジトリ直下 instance/。"""
    try:
        from flask import current_app
        return Path(current_app.instance_path)
    except Exception:
        return _FALLBACK_INSTANCE


def _save_local(source, key: str) -> str:
    """従来動作: instance/<key> に保存し、保存先の絶対パスを返す。"""
    dest = _instance_dir() / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = _as_bytes(source)
    with open(dest, "wb") as f:
        f.write(data)
    return str(dest)


def _save_s3(source, key: str, content_type: str = None) -> str:
    """S3 互換ストレージへアップロードし公開URLを返す。失敗時は例外を送出。"""
    import boto3  # 遅延import（未インストールなら ImportError → 呼び出し元でフォールバック）

    c = _s3_config()
    client = boto3.client(
        "s3",
        endpoint_url=c["endpoint_url"] or None,
        aws_access_key_id=c["access_key"],
        aws_secret_access_key=c["secret_key"],
        region_name=c["region"] or None,
    )
    data = _as_bytes(source)
    client.put_object(
        Bucket=c["bucket"],
        Key=key,
        Body=data,
        ContentType=_guess_content_type(key, content_type),
    )
    # 公開URL: PUBLIC_BASE_URL があれば優先、無ければ endpoint/bucket/key を組み立てる
    base = c["public_base_url"].rstrip("/") if c["public_base_url"] else ""
    if base:
        return f"{base}/{key}"
    if c["endpoint_url"]:
        return f"{c['endpoint_url'].rstrip('/')}/{c['bucket']}/{key}"
    return f"https://{c['bucket']}.s3.{c['region']}.amazonaws.com/{key}"


def _save_cloudinary(source, key: str, content_type: str = None) -> str:
    """Cloudinary へアップロードし secure_url を返す。失敗時は例外を送出。"""
    import cloudinary  # 遅延import
    import cloudinary.uploader

    # CLOUDINARY_URL 環境変数から自動設定される
    cloudinary.config()
    resource_type = "video" if _guess_content_type(key, content_type).startswith("video/") else "image"
    public_id = os.path.splitext(key)[0]
    result = cloudinary.uploader.upload(
        _as_bytes(source),
        public_id=public_id,
        resource_type=resource_type,
        overwrite=True,
    )
    return result.get("secure_url") or result.get("url")


def save(source, key: str, content_type: str = None) -> str:
    """
    生成物を保存する。

    Args:
        source: bytes もしくはローカルファイルパス(str/Path)
        key:    保存キー（例 "images/2026/xxx.png"）。ローカルでは instance/<key>。
        content_type: 省略時は key の拡張子から推定。

    Returns:
        永続化プロバイダが有効なら公開URL、無効/失敗ならローカル保存の絶対パス。
        ※ 例外は投げない（全断回避）。永続化に失敗してもローカルに残す。
    """
    key = key.lstrip("/")
    provider = active_provider()

    if provider == "s3":
        try:
            return _save_s3(source, key, content_type)
        except Exception as e:  # ImportError / 認証 / ネットワーク 等すべて
            logger.warning("storage: S3 upload failed (%s). falling back to local.", e)
    elif provider == "cloudinary":
        try:
            return _save_cloudinary(source, key, content_type)
        except Exception as e:
            logger.warning("storage: Cloudinary upload failed (%s). falling back to local.", e)

    return _save_local(source, key)
