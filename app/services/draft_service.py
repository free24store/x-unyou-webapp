import random
from .claude_client import call_claude


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

        drafts.append({
            "n": i + 1,
            "hook": hook,
            "target": target,
            "reinforcement": reinforcement,
            "education_name": edu["name"],
            "source": source,
            "text": text.strip(),
            "draft_type": "normal",
        })
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

        drafts.append({
            "n": i + 1,
            "hook": f"グダ消し: {guda['label']}",
            "target": guda["label"],
            "reinforcement": guda["desc"],
            "education_name": "グダ消し",
            "source": source,
            "text": text.strip(),
            "draft_type": "guda",
            "guda_id": gid,
        })
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

    return {
        "n": 1,
        "hook": f"ストーリー: {ki[:20]}…",
        "target": "ストーリー型",
        "reinforcement": "目新しさ・変化率・事件性",
        "education_name": "ストーリー型運用",
        "source": source,
        "text": text.strip(),
        "draft_type": "story",
    }


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
