"""
SNS投稿サービス
各プラットフォームのAPIを呼び出して投稿する。
credentials_json の構造は各関数の docstring を参照。

重要注意事項:
  X (Twitter): API v2 で投稿。X利用規約上の自動化に関するリスクはユーザー責任。
  Instagram: Meta Graph API。Business/Creator アカウント + アプリ審査が必要。
  TikTok: Content Posting API v2。開発者登録・OAuth2が必要。
  YouTube: Data API v3。Google Cloud プロジェクト + OAuth2が必要。
"""
import os
import json
import requests


# ---------------------------------------------------------------------------
# X (Twitter) — Tweepy v4 + API v2
# ---------------------------------------------------------------------------

def post_to_x(text: str, credentials: dict, media_path: str = None) -> str:
    """
    credentials keys:
      api_key, api_secret, access_token, access_token_secret
    Returns tweet ID string.
    """
    import tweepy
    client = tweepy.Client(
        consumer_key=credentials["api_key"],
        consumer_secret=credentials["api_secret"],
        access_token=credentials["access_token"],
        access_token_secret=credentials["access_token_secret"],
    )
    media_ids = None
    if media_path and os.path.exists(media_path):
        auth = tweepy.OAuth1UserHandler(
            credentials["api_key"], credentials["api_secret"],
            credentials["access_token"], credentials["access_token_secret"],
        )
        api_v1 = tweepy.API(auth)
        media = api_v1.media_upload(media_path)
        media_ids = [media.media_id]

    resp = client.create_tweet(text=text[:280], media_ids=media_ids)
    return str(resp.data["id"])


# ---------------------------------------------------------------------------
# Instagram — Meta Graph API v18
# ---------------------------------------------------------------------------

def post_to_instagram(caption: str, credentials: dict, image_path: str = None,
                      video_path: str = None) -> str:
    """
    credentials keys:
      access_token, instagram_business_account_id
    For image: image_url (publicly accessible URL) required.
    For video: video_url (publicly accessible URL) required.
    Returns Instagram media ID string.

    Note: ローカルファイルは直接送れない。CDN/S3等の公開URLが必要。
    """
    ig_id = credentials["instagram_business_account_id"]
    token = credentials["access_token"]
    base = f"https://graph.facebook.com/v18.0/{ig_id}"

    if video_path:
        # Reels投稿 (video_url が必要)
        video_url = credentials.get("video_url") or video_path
        container_data = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": token,
        }
    elif image_path:
        image_url = credentials.get("image_url") or image_path
        container_data = {
            "image_url": image_url,
            "caption": caption,
            "access_token": token,
        }
    else:
        raise ValueError("image_path or video_path required for Instagram")

    r = requests.post(f"{base}/media", data=container_data, timeout=30)
    r.raise_for_status()
    container_id = r.json()["id"]

    # Publish
    r2 = requests.post(f"{base}/media_publish",
                       data={"creation_id": container_id, "access_token": token},
                       timeout=30)
    r2.raise_for_status()
    return str(r2.json()["id"])


# ---------------------------------------------------------------------------
# TikTok — Content Posting API v2
# ---------------------------------------------------------------------------

def post_to_tiktok(caption: str, credentials: dict, video_path: str = None) -> str:
    """
    credentials keys:
      access_token
    Returns TikTok publish_id string.

    Direct Post API: ビデオファイルをチャンクアップロードして投稿。
    """
    token = credentials["access_token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    if not video_path or not os.path.exists(video_path):
        raise ValueError("video_path is required for TikTok")

    file_size = os.path.getsize(video_path)

    # Step 1: Initialize upload
    init_body = {
        "post_info": {
            "title": caption[:150],
            "privacy_level": "SELF_ONLY",  # 安全のためデフォルトは非公開
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        },
    }
    r = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers=headers,
        json=init_body,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()["data"]
    publish_id = data["publish_id"]
    upload_url = data["upload_url"]

    # Step 2: Upload video chunk
    with open(video_path, "rb") as f:
        video_data = f.read()
    upload_headers = {
        "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
        "Content-Type": "video/mp4",
    }
    r2 = requests.put(upload_url, headers=upload_headers, data=video_data, timeout=120)
    r2.raise_for_status()

    return str(publish_id)


# ---------------------------------------------------------------------------
# YouTube — Data API v3
# ---------------------------------------------------------------------------

def upload_to_youtube(title: str, description: str, credentials: dict,
                      video_path: str) -> str:
    """
    credentials keys:
      access_token, refresh_token, client_id, client_secret
    Returns YouTube video ID string.
    """
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials(
        token=credentials["access_token"],
        refresh_token=credentials["refresh_token"],
        client_id=credentials["client_id"],
        client_secret=credentials["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "22",  # People & Blogs
        },
        "status": {"privacyStatus": "private"},  # 安全のためデフォルト非公開
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return str(response["id"])


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def post_to_platform(platform: str, text: str, credentials: dict,
                     media_path: str = None) -> str:
    """プラットフォーム名に応じて適切な投稿関数を呼び出す。"""
    if platform == "x":
        return post_to_x(text, credentials, media_path)
    elif platform == "instagram":
        return post_to_instagram(text, credentials, video_path=media_path)
    elif platform == "tiktok":
        return post_to_tiktok(text, credentials, video_path=media_path)
    elif platform == "youtube":
        title = text.split("\n")[0][:100]
        return upload_to_youtube(title, text, credentials, media_path)
    else:
        raise ValueError(f"Unknown platform: {platform}")
