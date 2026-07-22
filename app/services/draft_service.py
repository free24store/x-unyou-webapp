import random
import re
from .claude_client import call_claude


# ──────────────────────────────────────────────
# E3-4: 自己改善ループ用の分類タグ
# ──────────────────────────────────────────────
# 実測ノウハウ: 「問いかけ＋CTA型は伸び、列挙・CTA無しは失速（約9倍差）」。
# → ドラフトを hook型/format/CTA有無で軽くタグ付けし、実績（インプ）と
#   突き合わせて勝ち型を可視化する。分類はルールベース（外部API不要）。
#   誇大な判定はせず、判別できないものは素直に既定カテゴリへ寄せる。

HOOK_TYPE_LABELS = {
    "question": "問いかけ型",
    "number": "数字型",
    "curiosity": "好奇心型",
    "authority": "権威・実績型",
    "statement": "断定型",
}
FORMAT_TYPE_LABELS = {
    "list": "列挙型",
    "howto": "ハウツー型",
    "story": "ストーリー型",
    "single": "単文型",
}
CTA_TYPE_LABELS = {
    "offer": "オファー導線あり",
    "follow": "フォロー誘導",
    "action": "行動喚起",
    "none": "CTAなし",
}


def classify_draft(text: str, cta_label: str = "", cta_url: str = "") -> dict:
    """ドラフト本文（＋CTA設定）から hook/format/cta を推定してタグを返す。

    ルールベースの軽い分類。外部APIには一切依存しない。
    返り値: {"hook_type": ..., "format_type": ..., "cta_type": ...}
    """
    t = text or ""

    # hook_type: 冒頭〜全体の訴求型
    if "？" in t or "?" in t:
        hook = "question"     # 問いかけ型（伸びやすい）
    elif any(k in t for k in ("実は", "驚", "衝撃", "禁断", "秘密", "知らない", "ヤバ")):
        hook = "curiosity"    # 好奇心型
    elif any(k in t for k in ("実績", "証明", "達成", "第一人者", "権威", "年収", "月収")):
        hook = "authority"    # 権威・実績型
    elif re.search(r"\d", t):
        hook = "number"       # 数字型
    else:
        hook = "statement"    # 断定型

    # format_type: 本文の構造
    bullet_count = t.count("・") + len(re.findall(r"(?m)^\s*[0-9①-⑨]+[\.\)、]", t))
    if bullet_count >= 2:
        fmt = "list"          # 列挙型（CTA無しだと失速しやすい）
    elif any(k in t for k in ("ステップ", "手順", "やり方", "方法", "コツ")):
        fmt = "howto"         # ハウツー型
    elif len(t) >= 140:
        fmt = "story"         # 長文/ストーリー型
    else:
        fmt = "single"        # 単文型

    # cta_type: CTAの有無・種類。フォーム側のCTA設定が最優先。
    if (cta_label or "").strip() or (cta_url or "").strip():
        cta = "offer"         # オファー導線あり（LP/セールス等）
    elif any(k in t for k in ("フォロー", "プロフィール", "見逃さない", "続きが気になる")):
        cta = "follow"        # フォロー誘導
    elif "→" in t and any(k in t for k in ("見て", "チェック", "登録", "こちら", "DM", "リンク", "詳しく")):
        cta = "action"        # 行動喚起
    else:
        cta = "none"          # CTAなし（失速要因）

    return {"hook_type": hook, "format_type": fmt, "cta_type": cta}


def _tag(draft: dict) -> dict:
    """生成結果 dict に分類タグを付与して返す（生成ロジックは壊さない）。"""
    draft.update(classify_draft(draft.get("text", "")))
    return draft


def template_draft(profile, hook, target, reinforcement, edu):
    return (
        f"【{profile.get('genre', '')}】\n"
        f"{hook}。\n"
        f"{target}って、実は{reinforcement}んです。\n"
        f"{edu['desc']}\n"
        f"→ 続きが気になる人はフォローして見逃さないでください。"
    )


def generate_drafts(count: int, profile: dict, vocab: dict, education_id: str = None) -> list:
    """通常ドラフト（フック×興味×強化要素×教育）を生成する。"""
    rnd = random.Random()
    edu_pool = vocab["education_stages_basic"] + vocab["education_stages_boost"]

    drafts = []
    for i in range(count):
        hook = rnd.choice(vocab["hooks"])
        target = rnd.choice(vocab["interest_targets"])
        reinforcement = rnd.choice(vocab["reinforcements"])
        if education_id:
            edu = next((e for e in edu_pool if e["id"] == education_id), None) or rnd.choice(edu_pool)
        else:
            edu = rnd.choice(edu_pool)

        prompt = (
            "あなたはX(旧Twitter)の投稿文案を作るコピーライターです。以下の設計図に従って、"
            "日本語で140字程度の投稿文を1つだけ作成してください。誇大な収益保証や捏造実績は書かないこと。"
            "出力は投稿文のみ（説明や前置き不要）。\n\n"
            f"ジャンル: {profile.get('genre', '')}\n"
            f"ターゲット(Who): {profile.get('who', '')}\n"
            f"ゴール(What): {profile.get('what', '')}\n"
            f"フック: {hook}\n"
            f"興味の対象: {target}\n"
            f"強化要素: {reinforcement}\n"
            f"教育要素: {edu['name']} - {edu['desc']}\n"
            f"教育要素の指示: {edu['prompt']}\n"
        )

        text = call_claude(prompt)
        source = "claude"
        if not text:
            text = template_draft(profile, hook, target, reinforcement, edu)
            source = "template"

        drafts.append(_tag({
            "n": i + 1,
            "hook": hook,
            "target": target,
            "reinforcement": reinforcement,
            "education_name": edu["name"],
            "source": source,
            "text": text.strip(),
            "draft_type": "normal",
        }))
    return drafts


def generate_guda_drafts(guda_ids: list, profile: dict, vocab: dict) -> list:
    """グダ消しドラフト: 指定した「買わない理由（グダ）」を1つずつ潰す投稿を生成する。"""
    guda_pool = {g["id"]: g for g in vocab.get("guda_items", [])}
    drafts = []
    for i, gid in enumerate(guda_ids):
        guda = guda_pool.get(gid)
        if not guda:
            continue

        prompt = (
            "あなたはX(旧Twitter)の投稿文案を作るコピーライターです。\n"
            "以下の「グダ（買わない理由）」を読者が自発的に解消できるよう、"
            "自然体で共感できる投稿文（140字程度）を1つだけ作成してください。\n"
            "説教や押し付けにならず、自分の体験談・事実ベースで書くこと。"
            "誇大表現・捏造実績はNG。出力は投稿文のみ。\n\n"
            f"ジャンル: {profile.get('genre', '')}\n"
            f"ターゲット(Who): {profile.get('who', '')}\n"
            f"立ち位置: {profile.get('position', '')}\n"
            f"実績: {profile.get('achievement', '')}\n"
            f"潰すグダ: 「{guda['label']}」\n"
            f"グダ解消のポイント: {guda['desc']}\n"
        )

        text = call_claude(prompt)
        source = "claude"
        if not text:
            text = (
                f"「{guda['label']}」と思っていませんか？\n"
                f"{guda['desc']}。\n"
                f"実際に{profile.get('position', '私')}がゼロから証明しています。\n"
                f"→ 詳しくはプロフィールを見てください。"
            )
            source = "template"

        drafts.append(_tag({
            "n": i + 1,
            "hook": f"グダ消し: {guda['label']}",
            "target": guda["label"],
            "reinforcement": guda["desc"],
            "education_name": "グダ消し",
            "source": source,
            "text": text.strip(),
            "draft_type": "guda",
            "guda_id": gid,
        }))
    return drafts


def generate_story_draft(story: dict, profile: dict) -> dict:
    """ストーリー型ドラフト: 起承転結×3要素（目新しさ・変化率・事件性）で投稿を生成する。"""
    ki = story.get("ki", "")      # 起：前提・出発点
    sho = story.get("sho", "")    # 承：進行中・途中経過
    ten = story.get("ten", "")    # 転：転機・事件
    ketsu = story.get("ketsu", "") # 結：オチ・現在地

    prompt = (
        "あなたはX(旧Twitter)のストーリー型投稿を作るコピーライターです。\n"
        "以下の起承転結ストーリーを元に、読者が「続きが気になる」「この人をフォローしたい」と思う\n"
        "投稿文（140〜280字程度）を1つだけ作成してください。\n"
        "結論を先に出さず、感情を揺らし、最後は次回への期待で終わること。\n"
        "誇大表現・捏造NG。出力は投稿文のみ。\n\n"
        f"ジャンル: {profile.get('genre', '')}\n"
        f"立ち位置: {profile.get('position', '')}\n"
        f"起（出発点・前提）: {ki}\n"
        f"承（進行中・途中経過）: {sho}\n"
        f"転（転機・事件・ピンチ）: {ten}\n"
        f"結（オチ・現在地）: {ketsu}\n"
    )

    text = call_claude(prompt)
    source = "claude"
    if not text:
        text = (
            f"【{profile.get('genre', '')}】\n"
            f"{ki}。\n"
            f"そこから{sho}という状況になったとき、{ten}が起きた。\n"
            f"結果、{ketsu}。\n"
            f"→ この続きはまた報告します。フォローしておいてください。"
        )
        source = "template"

    return _tag({
        "n": 1,
        "hook": f"ストーリー: {ki[:20]}…",
        "target": "ストーリー型",
        "reinforcement": "目新しさ・変化率・事件性",
        "education_name": "ストーリー型運用",
        "source": source,
        "text": text.strip(),
        "draft_type": "story",
    })


def generate_profile_bio(profile: dict) -> str:
    """プロフィール文（140字以内）をWho-What-Howから生成する。"""
    prompt = (
        "あなたはX(旧Twitter)のプロフィール文を作るコピーライターです。\n"
        "以下の情報を元に、フォローしたくなる魅力的なプロフィール文（4行・140字以内）を作成してください。\n"
        "4要素を必ず含めること: ①今やっていること ②実績（数字） ③自分の立ち位置 ④興味付け要素（不完全情報）\n"
        "誇大表現・捏造NG。出力はプロフィール文のみ。\n\n"
        f"ジャンル: {profile.get('genre', '')}\n"
        f"ターゲット(Who): {profile.get('who', '')}\n"
        f"ゴール(What): {profile.get('what', '')}\n"
        f"ロードマップ(How): {profile.get('how', '')}\n"
        f"立ち位置: {profile.get('position', '')}\n"
        f"実績: {profile.get('achievement', '')}\n"
    )

    text = call_claude(prompt)
    if not text:
        text = (
            f"{profile.get('what', '')}を目指す{profile.get('who', '')}向け発信。\n"
            f"{profile.get('position', '')}が{profile.get('achievement', '')}を達成した方法を発信中。\n"
            f"フル自動ではなく、再現性重視でロードマップを公開。\n"
            f"→ フォローして見逃さないでください。"
        )
    return text.strip()
