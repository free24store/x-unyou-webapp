"""
Xストーリーキャンペーンサービス
期間・フェーズに応じたストーリーライティングで投稿文を生成し、予約投稿を一括作成する。

ストーリーアーク（30日モデル）：
  認知期（1〜40%）  : 自分語り・悩み共感・問題提起
  信頼構築期（40〜75%）: 実績・価値提供・教育
  オファー期（75〜100%）: ベネフィット訴求・限定感・CTA
"""
import math
from datetime import date, timedelta, datetime

from .claude_client import call_claude


# ──────────── フェーズ定義 ────────────

PHASES = [
    {
        "name": "認知期",
        "ratio_end": 0.40,
        "arc": [
            "自己紹介・発信を始めたきっかけ",
            "自分が過去に抱えていた悩み・失敗談",
            "ターゲット読者の「あるある」な悩み",
            "業界の常識・思い込みへの問題提起",
            "読者が気づいていない見逃しがちな視点",
        ],
    },
    {
        "name": "信頼構築期",
        "ratio_end": 0.75,
        "arc": [
            "具体的な実績・ビフォーアフター",
            "読者にとって即役立つTIPSや知識",
            "よくある誤解を正す教育コンテンツ",
            "成功事例・お客様の声ストーリー",
            "自分の価値観・こだわりを語る",
        ],
    },
    {
        "name": "オファー期",
        "ratio_end": 1.00,
        "arc": [
            "サービスが解決できる具体的な悩み",
            "サービスのベネフィット（得られる未来）",
            "申し込みを迷っている人への背中押し",
            "限定感・期限・残り枠の告知",
            "最終CTA・申し込み案内",
        ],
    },
]


def _get_phase(ratio: float):
    """0〜1の比率からフェーズ情報を返す"""
    for ph in PHASES:
        if ratio < ph["ratio_end"]:
            return ph
    return PHASES[-1]


def _get_arc_theme(phase, day_in_phase: int) -> str:
    arc = phase["arc"]
    return arc[day_in_phase % len(arc)]


def generate_post_text(profile, phase_name, arc_theme, product_name="", day_no=1, total_days=30):
    """Claude APIで1投稿分のテキストを生成する（最大280字）"""
    genre = profile.genre or "X運用"
    name = profile.display_name or ""
    who = profile.who or "フォロワーを増やしたい方"
    what = profile.what or "あなたのサービス"

    product_line = f"訴求する商品・サービス：{product_name}" if product_name else ""

    prompt = (
        f"Xの投稿文（ツイート）を1つ書いてください。\n"
        f"発信者：{name}（{genre}の専門家）\n"
        f"ターゲット：{who}\n"
        f"ストーリーフェーズ：{phase_name}（全体の{day_no}/{total_days}日目）\n"
        f"今日のテーマ：{arc_theme}\n"
        f"{product_line}\n\n"
        f"条件：\n"
        f"- 140字以内（Xの仕様）\n"
        f"- 共感・発見・行動を促す書き方\n"
        f"- ハッシュタグは使わない\n"
        f"- 末尾に改行を入れてCTAを1文つける場合は任意\n"
        f"- 出力は投稿テキストのみ"
    )
    result = call_claude(prompt, max_tokens=300)
    if result:
        return result.strip()
    # フォールバック
    return f"【{phase_name} Day{day_no}】{arc_theme}について。{who}の皆さんにとって参考になれば嬉しいです。"


def generate_image_prompt(profile, arc_theme, phase_name):
    """DALL-E 3用の画像プロンプトをClaudeで生成する"""
    genre = profile.genre or "X運用"
    prompt = (
        f"An eye-catching social media image for a Japanese X (Twitter) post.\n"
        f"Topic: {arc_theme} (phase: {phase_name}, niche: {genre})\n"
        f"Style: clean, modern, professional, motivational\n"
        f"No text overlay. Square format (1:1). Bright and engaging colors.\n"
        f"Output: English image generation prompt only (max 100 words)."
    )
    result = call_claude(prompt, max_tokens=150)
    return result.strip() if result else f"Professional social media illustration for {genre}, motivational, clean design"


def build_campaign_schedule(profile, start_date, end_date, posts_per_day=1, product_name=""):
    """
    キャンペーン全日程の投稿スケジュールを生成して返す。

    Returns:
        list of dict: [{date, scheduled_time, text, phase, arc_theme, image_prompt?}]
    """
    total_days = (end_date - start_date).days + 1
    schedule = []

    # 投稿時刻スロット（posts_per_dayに応じて分散）
    time_slots = _get_time_slots(posts_per_day)

    day_no = 0
    current_date = start_date
    while current_date <= end_date:
        day_no += 1
        ratio = (day_no - 1) / max(total_days - 1, 1)
        phase = _get_phase(ratio)
        phase_name = phase["name"]

        # フェーズ内での日数（アークテーマのサイクル用）
        prev_days = sum(
            math.floor(total_days * ph["ratio_end"]) - (
                math.floor(total_days * PHASES[i - 1]["ratio_end"]) if i > 0 else 0
            )
            for i, ph in enumerate(PHASES)
            if ph["name"] == phase_name
            for _ in [None]  # dummy loop to get index
        )
        day_in_phase = day_no - 1  # シンプルにday_noベースでサイクル

        arc_theme = _get_arc_theme(phase, day_in_phase)

        for slot_idx, time_slot in enumerate(time_slots):
            scheduled_dt = datetime.combine(current_date, time_slot)
            schedule.append({
                "date": current_date,
                "scheduled_at": scheduled_dt,
                "phase": phase_name,
                "arc_theme": arc_theme,
                "slot": slot_idx,
                "day_no": day_no,
                "total_days": total_days,
            })

        current_date += timedelta(days=1)

    return schedule


def _get_time_slots(posts_per_day: int):
    """投稿時刻スロットを生成する（日本時間で効果的な時間帯）"""
    import datetime as dt
    if posts_per_day == 1:
        return [dt.time(8, 0)]   # 朝8時
    elif posts_per_day == 2:
        return [dt.time(8, 0), dt.time(20, 0)]
    elif posts_per_day == 3:
        return [dt.time(7, 0), dt.time(12, 0), dt.time(20, 0)]
    else:
        # 4投稿以上は均等分散
        hours = [int(7 + i * (16 / (posts_per_day - 1))) for i in range(posts_per_day)]
        return [dt.time(h, 0) for h in hours]


def create_campaign_posts(campaign, profile, db_session, generate_texts=True):
    """
    キャンペーンの全投稿をDBに保存する。
    generate_texts=True のとき Claude でテキストを生成、False のときはプレースホルダー。
    """
    from ..models import ScheduledPost

    start_date = campaign.start_date
    end_date = campaign.end_date
    schedule = build_campaign_schedule(
        profile, start_date, end_date,
        posts_per_day=campaign.posts_per_day,
        product_name=campaign.product_name or "",
    )

    posts = []
    for item in schedule:
        if generate_texts:
            text = generate_post_text(
                profile,
                item["phase"],
                item["arc_theme"],
                product_name=campaign.product_name or "",
                day_no=item["day_no"],
                total_days=item["total_days"],
            )
        else:
            text = f"【{item['phase']} Day{item['day_no']}】{item['arc_theme']}"

        post = ScheduledPost(
            client_id=campaign.client_id,
            platform=campaign.platform,
            text=text,
            scheduled_at=item["scheduled_at"],
            status="pending",
            created_by_user_id=campaign.created_by_user_id,
        )
        db_session.add(post)
        posts.append(post)

    campaign.total_posts = len(posts)
    campaign.status = "done"
    db_session.commit()
    return posts
