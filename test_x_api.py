#!/usr/bin/env python3
"""
X (Twitter) API 疎通テストスクリプト
======================================
取得したAPIキーが正しく動作するかを確認します。
テスト投稿を1件行い、確認後すぐに削除します。

使い方:
  # 環境変数で渡す場合
  export X_API_KEY="your_api_key"
  export X_API_SECRET="your_api_secret"
  export X_ACCESS_TOKEN="your_access_token"
  export X_ACCESS_TOKEN_SECRET="your_access_token_secret"
  python test_x_api.py

  # コマンドライン引数で渡す場合
  python test_x_api.py \
    --api-key YOUR_API_KEY \
    --api-secret YOUR_API_SECRET \
    --access-token YOUR_ACCESS_TOKEN \
    --access-token-secret YOUR_ACCESS_TOKEN_SECRET

依存:
  pip install tweepy
"""

import os
import sys
import argparse
import time


def parse_args():
    parser = argparse.ArgumentParser(
        description="X (Twitter) API 疎通テスト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--api-key", default=None, help="API Key (Consumer Key)")
    parser.add_argument("--api-secret", default=None, help="API Secret (Consumer Secret)")
    parser.add_argument("--access-token", default=None, help="Access Token")
    parser.add_argument("--access-token-secret", default=None, help="Access Token Secret")
    parser.add_argument("--no-delete", action="store_true", help="テスト投稿を削除しない（確認用）")
    return parser.parse_args()


def get_credentials(args):
    """コマンドライン引数 > 環境変数 の優先順位で認証情報を取得"""
    api_key = args.api_key or os.environ.get("X_API_KEY")
    api_secret = args.api_secret or os.environ.get("X_API_SECRET")
    access_token = args.access_token or os.environ.get("X_ACCESS_TOKEN")
    access_token_secret = args.access_token_secret or os.environ.get("X_ACCESS_TOKEN_SECRET")

    missing = []
    if not api_key:
        missing.append("API Key (--api-key または X_API_KEY 環境変数)")
    if not api_secret:
        missing.append("API Secret (--api-secret または X_API_SECRET 環境変数)")
    if not access_token:
        missing.append("Access Token (--access-token または X_ACCESS_TOKEN 環境変数)")
    if not access_token_secret:
        missing.append("Access Token Secret (--access-token-secret または X_ACCESS_TOKEN_SECRET 環境変数)")

    if missing:
        print("\n[ERROR] 以下の認証情報が不足しています:")
        for m in missing:
            print(f"  - {m}")
        print("\n使い方: python test_x_api.py --help")
        sys.exit(1)

    return api_key, api_secret, access_token, access_token_secret


def main():
    args = parse_args()

    print("=" * 55)
    print("X (Twitter) API 疎通テスト")
    print("=" * 55)

    # 認証情報を取得
    api_key, api_secret, access_token, access_token_secret = get_credentials(args)
    print("\n[1/4] 認証情報を確認中...")
    print(f"  API Key:             {api_key[:8]}...{api_key[-4:]}")
    print(f"  API Secret:          {api_secret[:8]}...{api_secret[-4:]}")
    print(f"  Access Token:        {access_token[:8]}...{access_token[-4:]}")
    print(f"  Access Token Secret: {access_token_secret[:8]}...{access_token_secret[-4:]}")

    # tweepy インポート確認
    try:
        import tweepy
    except ImportError:
        print("\n[ERROR] tweepy がインストールされていません。")
        print("  実行してください: pip install tweepy")
        sys.exit(1)

    # クライアント初期化
    print("\n[2/4] X API に接続中...")
    try:
        client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )
        # 自分のアカウント情報を取得して接続確認
        me = client.get_me()
        if me.data:
            print(f"  接続成功! アカウント: @{me.data.username} ({me.data.name})")
        else:
            print("  [WARNING] アカウント情報を取得できませんでした")
    except tweepy.Unauthorized:
        print("\n[ERROR] 認証に失敗しました。")
        print("  確認事項:")
        print("  - APIキーが正しいか")
        print("  - Access Token が「Read and Write」権限で生成されているか")
        print("  - Developer Portalでアプリの権限を変更した場合、Access Tokenを再生成したか")
        sys.exit(1)
    except tweepy.Forbidden:
        print("\n[ERROR] アクセスが拒否されました（403 Forbidden）。")
        print("  確認事項:")
        print("  - X Developer Portal でアプリの「User authentication settings」を確認")
        print("  - 「Read and Write」権限が有効になっているか")
        print("  - Basic 以上のアクセスレベルが必要な場合があります")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] 接続に失敗しました: {e}")
        sys.exit(1)

    # テスト投稿
    print("\n[3/4] テスト投稿を送信中...")
    test_text = f"[APIテスト] x-unyou-webapp の接続確認投稿です。このツイートはすぐに削除されます。({int(time.time())})"
    try:
        response = client.create_tweet(text=test_text)
        tweet_id = response.data["id"]
        print(f"  投稿成功! Tweet ID: {tweet_id}")
        print(f"  URL: https://x.com/i/web/status/{tweet_id}")
    except tweepy.Forbidden as e:
        print(f"\n[ERROR] 投稿に失敗しました（権限不足）: {e}")
        print("  「Read and Write」権限が必要です。")
        print("  Developer Portal > アプリ設定 > User authentication settings を確認してください。")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] 投稿に失敗しました: {e}")
        sys.exit(1)

    # テスト投稿を削除
    if not args.no_delete:
        print("\n[4/4] テスト投稿を削除中...")
        time.sleep(2)  # 少し待ってから削除
        try:
            client.delete_tweet(tweet_id)
            print(f"  削除成功! Tweet ID: {tweet_id}")
        except Exception as e:
            print(f"  [WARNING] 削除に失敗しました: {e}")
            print(f"  手動で削除してください: https://x.com/i/web/status/{tweet_id}")
    else:
        print("\n[4/4] --no-delete オプションのため削除をスキップしました")
        print(f"  投稿URL: https://x.com/i/web/status/{tweet_id}")

    print("\n" + "=" * 55)
    print("テスト完了! APIキーは正しく動作しています。")
    print("x-unyou-webapp の SNS設定画面に入力してください。")
    print("  URL: http://127.0.0.1:5050/sns/settings")
    print("=" * 55)


if __name__ == "__main__":
    main()
