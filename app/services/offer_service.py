"""
E6-1: 投稿→オファー導線（CTA）サービス

投稿本文の末尾にオファー（LP / 任意URL）への導線を織り込む。
外部API非依存。テンプレート／手動フローで常に動作する。
"""
from flask import url_for

from ..models import LandingPage


def resolve_offer_url(client_id, offer_lp_id=None, raw_url=""):
    """CTAリンク先URLを解決して返す。

    - offer_lp_id が指定され、その LP が当該clientの published なら公開LPのURLを返す。
      （公開LPは client 単位で最新 published を返す設計のため、id指名ではなく
        client_id ベースで url_for する）
    - LP が解決できなければ raw_url を返す。
    - どちらも無ければ空文字を返す。
    """
    if offer_lp_id:
        try:
            lp_id = int(offer_lp_id)
        except (TypeError, ValueError):
            lp_id = None
        if lp_id:
            lp = LandingPage.query.filter_by(
                id=lp_id, client_id=client_id, is_published=True
            ).first()
            if lp:
                return url_for("public.lp_view", client_id=client_id, _external=False)

    return (raw_url or "").strip()


def compose_with_cta(text, cta_label, cta_url):
    """本文末尾にCTAを付与した文字列を返す。

    - cta_url が空なら text をそのまま返す。
    - cta_label が空なら URL のみを付与する。
    - 付与時は本文との間に空行（改行2つ）を挟む。
    """
    text = text or ""
    cta_url = (cta_url or "").strip()
    if not cta_url:
        return text

    cta_label = (cta_label or "").strip()
    cta_line = f"{cta_label} {cta_url}".strip() if cta_label else cta_url
    return f"{text}\n\n{cta_line}"
