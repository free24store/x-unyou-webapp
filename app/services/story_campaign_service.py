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


_POST_TEMPLATES = {
    "自己紹介・発信を始めたきっかけ": (
        "{name_pref}なぜ{genre}を発信しているか、正直に話します。\n\n"
        "きっかけは{who}が抱える悩みを\n"
        "自分自身も経験していたから。\n\n"
        "解決策を見つけて変われた経験を\n"
        "あなたにも届けたい——それだけです。\n\n"
        "これからもよろしくお願いします🙏"
    ),
    "自分が過去に抱えていた悩み・失敗談": (
        "{name_pref}正直な失敗談を話します。\n\n"
        "以前の私は{genre}を頑張っていたのに\n"
        "全然成果が出ませんでした。\n\n"
        "理由は「正しい方法を知らなかった」だけ。\n\n"
        "あなたが今悩んでいるなら\n"
        "それは才能の問題ではありません💪"
    ),
    "ターゲット読者の「あるある」な悩み": (
        "{who}の「あるある」\n\n"
        "✗ 毎日投稿してるのに伸びない\n"
        "✗ 何を書けばいいかわからない\n"
        "✗ 数字が上がらず心が折れそう\n\n"
        "1つでも当てはまったら\n"
        "方法論を変えるサインかもしれません。\n\n"
        "今日から変えていきましょう🔥"
    ),
    "業界の常識・思い込みへの問題提起": (
        "{genre}の「常識」が実は間違っている。\n\n"
        "「毎日投稿すれば伸びる」\n"
        "→ 内容の質が低いと逆効果\n\n"
        "「フォロワーが多い人が正しい」\n"
        "→ 数より質が売上を決める\n\n"
        "正しいやり方を知っている人だけが\n"
        "結果を出し続けます。"
    ),
    "読者が気づいていない見逃しがちな視点": (
        "{who}が見落としがちな視点。\n\n"
        "{genre}で大切なのは\n"
        "「何を発信するか」より\n"
        "「誰に発信するか」です。\n\n"
        "ターゲットが明確な投稿は\n"
        "少ないフォロワーでも刺さります。\n\n"
        "まず一人のために書いてみて。"
    ),
    "具体的な実績・ビフォーアフター": (
        "ビフォー→アフターを公開します。\n\n"
        "【3ヶ月前】\n"
        "フォロワー：87人\n"
        "月収：副業0円\n\n"
        "【現在】\n"
        "フォロワー：1,230人\n"
        "月収：副業6.5万円\n\n"
        "変えたのは「発信の型」だけ。\n"
        "{who}もできます💪"
    ),
    "読者にとって即役立つTIPSや知識": (
        "{genre}で即使える3つのコツ。\n\n"
        "①最初の1行に「読者の悩み」を入れる\n"
        "②箇条書きで読みやすくする\n"
        "③最後に一つだけ行動を促す\n\n"
        "この3つだけで\n"
        "エンゲージメントが変わります。\n\n"
        "今日から試してみてください✨"
    ),
    "よくある誤解を正す教育コンテンツ": (
        "{genre}の誤解トップ3。\n\n"
        "✗「フォロワーが多い=稼げる」→間違い\n"
        "✗「毎日投稿=必ず伸びる」→間違い\n"
        "✗「バズれば解決する」→間違い\n\n"
        "正解は「質の高い発信を\n"
        "正しいターゲットに届け続けること」\n\n"
        "シンプルだけど、これだけです。"
    ),
    "成功事例・お客様の声ストーリー": (
        "先日、嬉しいご報告をいただきました。\n\n"
        "「3ヶ月前まで何を投稿すればいいか\n"
        "全くわからなかったのに\n"
        "今では毎日迷わず投稿できています」\n\n"
        "変わったのは\n"
        "「方法」を知ったことだけ。\n\n"
        "あなたにも同じ変化が起きます🙏"
    ),
    "自分の価値観・こだわりを語る": (
        "{name_pref}大切にしていること。\n\n"
        "{who}の「本当の悩み」に\n"
        "正直に向き合うこと。\n\n"
        "売れる型を教えるより\n"
        "あなただけの型を見つける手伝いを。\n\n"
        "それが{genre}サポートへの\n"
        "私のこだわりです。"
    ),
    "サービスが解決できる具体的な悩み": (
        "{who}のために作りました。\n\n"
        "こんな悩みを抱えていませんか？\n"
        "・何を投稿すれば伸びるかわからない\n"
        "・継続できず途中で止まってしまう\n"
        "・数字が出なくて自信をなくしている\n\n"
        "その悩み、全部解決できます。\n"
        "詳細はプロフィールリンクから👆"
    ),
    "サービスのベネフィット（得られる未来）": (
        "3ヶ月後のあなたを想像してください。\n\n"
        "✓ 毎日迷わず投稿できている\n"
        "✓ フォロワーが着実に増えている\n"
        "✓ DM相談が来るようになっている\n\n"
        "これは夢ではなく\n"
        "正しい方法を知れば\n"
        "誰でも辿り着ける未来です。\n\n"
        "一緒に行きましょう🔥"
    ),
    "申し込みを迷っている人への背中押し": (
        "「もう少し様子を見てから」\n\n"
        "その気持ち、すごくわかります。\n\n"
        "でも、1年後の自分を想像してみて。\n\n"
        "今と同じことを繰り返して\n"
        "また「もう少し様子を見よう」って\n"
        "言っていませんか？\n\n"
        "変わるなら、今日が一番早い。"
    ),
    "限定感・期限・残り枠の告知": (
        "【残りわずか】\n\n"
        "今月のサポート枠、残り2名です。\n\n"
        "{who}で\n"
        "本気で変わりたい方のみ\n"
        "お申し込みください。\n\n"
        "詳細はプロフィールのリンクから👆\n\n"
        "期限を過ぎたらご案内できません🙏"
    ),
    "最終CTA・申し込み案内": (
        "最後にご案内します。\n\n"
        "{who}が{what}を\n"
        "実現するためのサポートをしています。\n\n"
        "✓ マンツーマンサポート\n"
        "✓ 実績ある型を提供\n"
        "✓ 質問はLINEでいつでも\n\n"
        "詳細・お申し込みはプロフィールから。\n"
        "お待ちしています🙏"
    ),
}


def generate_post_text(profile, phase_name, arc_theme, product_name="", day_no=1, total_days=30):
    """投稿文を生成する（テンプレートファースト、API は任意強化）"""
    genre = profile.genre or "X運用"
    name  = profile.display_name or ""
    who   = profile.who or "フォロワーを増やしたい方"
    what  = profile.what or "あなたのサービス"
    name_pref = f"{name}です。\n\n" if name else ""

    # テンプレートベースの本文
    tmpl = _POST_TEMPLATES.get(arc_theme, (
        "【{phase_name} Day{day_no}】\n\n"
        "{arc_theme}について。\n\n"
        "{who}の皆さん、\n"
        "ひとつずつ一緒に変えていきましょう💪"
    ))
    fallback = tmpl.format(
        name_pref=name_pref,
        genre=genre,
        who=who,
        what=what,
        product=product_name or what,
        phase_name=phase_name,
        day_no=day_no,
        arc_theme=arc_theme,
    )

    # Claude がある場合に強化（オプション）
    product_line = f"訴求する商品・サービス：{product_name}" if product_name else ""
    result = call_claude(
        f"Xの投稿文（ツイート）を1つ書いてください。\n"
        f"発信者：{name}（{genre}の専門家）、ターゲット：{who}\n"
        f"フェーズ：{phase_name}（{day_no}/{total_days}日目）、テーマ：{arc_theme}\n"
        f"{product_line}\n"
        f"条件：140字以内・共感/発見/行動を促す・ハッシュタグなし・出力は本文のみ",
        max_tokens=300
    )
    return result.strip() if result else fallback


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
