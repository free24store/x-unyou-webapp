"""E3-7: 日次バッチ生成＋同文重複検出サービス。

ドクトリンv2の実測ノウハウに基づく「毎時24本・時報型・全て別内容」運用の下地。
時間帯×フォーマット配分に沿って 1日分（24枠）の投稿計画を組み、テンプレファースト
（Claudeキーが無くても成立）で本文を生成する。生成後は正規化＋difflib（標準ライブラリ）
で同文/近似重複を検出し、同文は絶対に通さない・近似は警告としてフラグする。

承認ゲート維持: このサービスは本文を作るだけで、外部（X）へは一切書き込まない。
保存はルート側で ScheduledPost を status="pending"（承認待ち）として積むだけ。
"""
import os
import re
import difflib

from .claude_client import call_claude


def is_available():
    """Claude（本文強化）が使えるか。無くてもテンプレで成立する。"""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ──────────────────────────────────────────────
# 時間帯 × フォーマット配分（ドクトリンv2）
# ──────────────────────────────────────────────
# 各バンドは連続する時間帯に1つ以上のフォーマットを割り当てる。
# 複数フォーマットのバンドは時間ごとに巡回して割り当てる（例: 問いかけ→検索→…）。
# hours の合計は 0〜23 の 24枠をちょうど覆う。
BANDS = [
    {"label": "深夜・軽め",         "hours": [0, 1, 2, 3, 4, 5], "formats": ["light"]},
    {"label": "朝・問いかけ/検索",   "hours": [6, 7, 8, 9],       "formats": ["question", "search"]},
    {"label": "昼前・Tips",         "hours": [10, 11, 12],       "formats": ["tips"]},
    {"label": "昼過ぎ・検索/あるある", "hours": [13, 14, 15],       "formats": ["search", "relatable"]},
    {"label": "夕方・再活用/引用RP",  "hours": [16, 17, 18],       "formats": ["repurpose", "quote_rp"]},
    {"label": "夜・問いかけ",        "hours": [19, 20],           "formats": ["question"]},
    {"label": "深夜前・保存系",      "hours": [21, 22, 23],       "formats": ["save"]},
]

FORMAT_LABELS = {
    "question":  "問いかけ",
    "search":    "検索誘導",
    "tips":      "Tips",
    "relatable": "あるある",
    "repurpose": "再活用",
    "quote_rp":  "引用RP",
    "save":      "保存系",
    "light":     "軽め",
}

# フォーマット別テンプレ雛形（骨子＋プレースホルダ）。
# 同じフォーマットが同日に複数回出ても骨子が被らないよう、各フォーマットに
# 複数バリアントを用意し、出現回数で巡回する。プレースホルダは profile/vocab で埋める。
FORMAT_SKELETONS = {
    "question": [
        "{genre}をやっていて、こんな風に感じたことはありませんか？\n\n"
        "「{target}、結局どれが正解なんだろう」\n\n"
        "実は{reinforcement}やり方があります。\nあなたはどう向き合っていますか？リプで教えてください。",
        "ふと気になったので聞かせてください。\n\n"
        "{who}にとって、{target}って\n本当に必要だと思いますか？\n\n"
        "個人的には{reinforcement}アプローチに寄せています。\nあなたの意見も知りたいです。",
        "{hour}時、問いかけをひとつ。\n\n"
        "もし{target}を今日ひとつだけ変えるなら\nあなたは何を選びますか？\n\n"
        "{reinforcement}選択を、私は推します。",
    ],
    "search": [
        "【保存版】{who}が今すぐ調べるべきこと。\n\n"
        "「{target}」で検索してみてください。\n\n"
        "{reinforcement}情報にたどり着けます。\n知っているかどうかで差がつきます。",
        "調べ方ひとつで結果が変わります。\n\n"
        "{genre}に迷ったら\n「{target}」を軸に情報を集めてみて。\n\n"
        "{reinforcement}ヒントが見つかるはずです。",
        "検索のコツ。\n\n"
        "ただ「{target}」と打つのではなく、\n自分の状況を足して調べると精度が上がります。\n\n"
        "{reinforcement}答えに最短でたどり着けます。",
    ],
    "tips": [
        "{genre}のTips。\n\n"
        "{target}を扱うときのコツは\n「{reinforcement}」を意識すること。\n\n"
        "これだけで結果が変わります。\n今日から試してみてください。",
        "地味だけど効くTips。\n\n"
        "{target}は、完璧を狙わず\n{reinforcement}形で小さく始めるのが正解。\n\n"
        "続けられる人が最後に勝ちます。",
        "今日の実践Tips。\n\n"
        "① {target}を1つに絞る\n② {reinforcement}やり方で試す\n③ 反応を見て直す\n\n"
        "シンプルですが、これが一番効きます。",
    ],
    "relatable": [
        "{who}あるある。\n\n"
        "・{target}を追いかけて疲れる\n・情報が多すぎて動けない\n・気づけば1日が終わっている\n\n"
        "1つでも当てはまったら、\n{reinforcement}やり方に変えるサインです。",
        "たぶん{who}なら共感してくれるはず。\n\n"
        "{target}、頭ではわかってるのに\n手が動かない日ってありますよね。\n\n"
        "そんな日は{reinforcement}一歩でOKです。",
    ],
    "repurpose": [
        "以前も話しましたが、大事なのでもう一度。\n\n"
        "{genre}で成果を出す人は\n{target}を「{reinforcement}」形で使っています。\n\n"
        "過去の自分に教えたいくらい、ここが分岐点でした。",
        "反応が良かったので再掲します。\n\n"
        "{target}について、\n結局は{reinforcement}が最短だという話。\n\n"
        "{who}に届いてほしい内容です。",
    ],
    "quote_rp": [
        "この視点、すごく大事だと思う。\n\n"
        "{target}について、\n「{reinforcement}」という考え方に強く共感しました。\n\n"
        "{who}こそ意識してほしいポイントです。",
        "引用しながら補足します。\n\n"
        "{target}を語るとき、\n{reinforcement}という前提が抜けがち。\n\n"
        "ここを押さえると{genre}の見え方が変わります。",
    ],
    "save": [
        "【保存推奨】{genre}で迷ったら見返すリスト。\n\n"
        "□ {target}を押さえる\n□ {reinforcement}を徹底する\n□ 毎日ひとつ行動する\n\n"
        "寝る前に見返すと、明日の一歩が決まります。",
        "あとで読み返せるように保存版。\n\n"
        "{target}でつまずいたら\n「{reinforcement}」に立ち返ってください。\n\n"
        "{who}のための備忘録です。",
        "ブックマーク推奨。\n\n"
        "{genre}の土台は\n① {target} ② {reinforcement} の2点。\n\n"
        "忘れたころに効いてくるので、保存しておいて。",
    ],
    "light": [
        "（{hour}時のひとりごと）\n\n"
        "{genre}を続けていて思うのは、\n結局「{reinforcement}」が一番強いということ。\n\n"
        "こんな時間に見てくれてる人、いつもありがとう。",
        "{hour}時。少しだけ本音を。\n\n"
        "{target}に振り回された日もあったけど、\n今は{reinforcement}ペースが心地いい。\n\n"
        "焦らずいきましょう。",
        "静かな{hour}時に、ゆるく一言。\n\n"
        "{who}の毎日が\n少しでも軽くなりますように。\n\n"
        "{reinforcement}くらいがちょうどいい。",
        "{hour}時のメモ。\n\n"
        "うまくいかない日は\n「{reinforcement}」だけ覚えておけば十分。\n\n"
        "明日の自分に任せて、今日は休もう。",
        "深夜の{hour}時、思うこと。\n\n"
        "{genre}は短距離走じゃない。\n{reinforcement}姿勢で長く続けた人が残る。\n\n"
        "おやすみなさい。",
        "{hour}時、ふと。\n\n"
        "{target}を頑張るあなたへ。\n完璧じゃなくていい、{reinforcement}で十分です。\n\n"
        "また明日、続きをやりましょう。",
    ],
}


def build_batch_plan():
    """1日分（24枠）の生成計画を返す。各枠は hour（0-23）にフォーマットを割り当てる。

    Returns:
        list[dict]: [{hour, minute, band_label, format_key, format_label}] を hour 昇順で。
    """
    plan = [None] * 24
    for band in BANDS:
        formats = band["formats"]
        for i, hour in enumerate(band["hours"]):
            fmt = formats[i % len(formats)]
            plan[hour] = {
                "hour": hour,
                "minute": 0,
                "band_label": band["label"],
                "format_key": fmt,
                "format_label": FORMAT_LABELS.get(fmt, fmt),
            }
    # 念のため未割当（想定外）はスキップせず埋める
    return [slot for slot in plan if slot is not None]


def band_summary():
    """プレビュー用: バンドごとのフォーマット配分と枠数を返す。"""
    rows = []
    for band in BANDS:
        rows.append({
            "label": band["label"],
            "hours": band["hours"],
            "hour_range": "{:02d}:00〜{:02d}:59".format(band["hours"][0], band["hours"][-1]),
            "count": len(band["hours"]),
            "formats": [FORMAT_LABELS.get(f, f) for f in band["formats"]],
        })
    return rows


def _pick(seq, idx):
    return seq[idx % len(seq)] if seq else ""


def _template_text(slot, occ, profile, vocab):
    """テンプレ雛形（骨子＋プレースホルダ）で1枠分の本文を作る。Claude不要で成立。"""
    hooks = vocab.get("hooks", [])
    targets = vocab.get("interest_targets", [])
    reinforcements = vocab.get("reinforcements", [])

    hour = slot["hour"]
    # 枠ごとに語彙をずらして、同フォーマットでも中身が別になるようにする。
    target = _pick(targets, hour * 2 + occ) or "発信のテーマ"
    reinforcement = _pick(reinforcements, hour + occ * 3) or "無理なく続けられる"
    _pick(hooks, hour)  # フックはトーンの参考（本文には直接使わない枠もある）

    variants = FORMAT_SKELETONS.get(slot["format_key"], FORMAT_SKELETONS["light"])
    skeleton = variants[occ % len(variants)]

    text = skeleton.format(
        genre=profile.get("genre") or "X運用",
        who=profile.get("who") or "発信で伸ばしたい人",
        what=profile.get("what") or "あなたのサービス",
        target=target,
        reinforcement=reinforcement,
        hour=hour,
    )
    return text.strip()


def _claude_text(slot, profile):
    """Claudeがあれば本文を強化する。無ければ None（呼び出し側でテンプレにフォールバック）。"""
    if not is_available():
        return None
    prompt = (
        "Xの投稿文（ツイート）を1つだけ書いてください。出力は本文のみ。\n"
        f"発信テーマ: {profile.get('genre', '')}\n"
        f"ターゲット: {profile.get('who', '')}\n"
        f"投稿の型: {slot['format_label']}（{slot['band_label']}／{slot['hour']}時台）\n"
        "条件: 140字以内・型に沿う・誇大表現や捏造実績はNG・ハッシュタグなし・"
        "他の投稿と内容が被らないよう独自の切り口にすること。"
    )
    result = call_claude(prompt, max_tokens=300)
    return result.strip() if result else None


def generate_batch(profile, vocab, plan=None, use_claude=True):
    """1日分（24本）の投稿本文を生成する。テンプレファースト。

    Args:
        profile: dict（genre/who/what/... を含む）。
        vocab: load_vocab() の戻り値。
        plan: build_batch_plan() の結果（省略時は内部で生成）。
        use_claude: Trueかつキーがあれば本文をClaudeで強化。無ければテンプレ。

    Returns:
        list[dict]: [{n, hour, minute, band_label, format_key, format_label, source, text}]
    """
    if plan is None:
        plan = build_batch_plan()

    # 同フォーマットの出現回数を数えてバリアントを巡回させる。
    occ_counter = {}
    items = []
    for i, slot in enumerate(plan):
        fmt = slot["format_key"]
        occ = occ_counter.get(fmt, 0)
        occ_counter[fmt] = occ + 1

        text = None
        source = "template"
        if use_claude:
            text = _claude_text(slot, profile)
            if text:
                source = "claude"
        if not text:
            text = _template_text(slot, occ, profile, vocab)
            source = "template"

        items.append({
            "n": i + 1,
            "hour": slot["hour"],
            "minute": slot["minute"],
            "band_label": slot["band_label"],
            "format_key": fmt,
            "format_label": slot["format_label"],
            "source": source,
            "text": text,
        })
    return items


# ──────────────────────────────────────────────
# 同文 / 近似重複の検出
# ──────────────────────────────────────────────
_NORMALIZE_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_text(text):
    """比較用の正規化: 小文字化・空白/記号除去。日本語の文字は残す。"""
    if not text:
        return ""
    return _NORMALIZE_RE.sub("", text.lower())


def similarity(a, b):
    """正規化後の文字列類似度（0.0〜1.0）。標準ライブラリ difflib を使用。"""
    na, nb = normalize_text(a), normalize_text(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def detect_duplicates(items, near_threshold=0.9):
    """生成した投稿群から同文/近似重複を検出してフラグを付ける。

    判定:
      - exact: 正規化後が完全一致（＝同文コピペ）。絶対に通してはいけない。
      - near : 正規化後の類似度が near_threshold 以上（同文ではないが酷似）。警告扱い。
    最初に出た1件を「基準（keep）」とし、以降の一致/酷似を dup としてフラグする。

    各 item に dup_status / dup_of（基準の n）/ dup_score を書き込み、
    集計 dict を返す（呼び出し側で除外/警告に使う）。
    """
    # 走査対象の順序を保ちつつ、既に採用した基準本文と比較する。
    kept = []  # [(index_in_items, normalized)]
    exact_pairs = []
    near_pairs = []

    for idx, it in enumerate(items):
        it["dup_status"] = "unique"
        it["dup_of"] = None
        it["dup_score"] = 0.0
        norm = normalize_text(it["text"])

        best_status = "unique"
        best_of = None
        best_score = 0.0
        for kidx, knorm in kept:
            if norm and norm == knorm:
                best_status = "exact"
                best_of = items[kidx]["n"]
                best_score = 1.0
                break
            score = similarity(it["text"], items[kidx]["text"])
            if score >= near_threshold and score > best_score:
                best_status = "near"
                best_of = items[kidx]["n"]
                best_score = score

        if best_status == "exact":
            it["dup_status"] = "exact"
            it["dup_of"] = best_of
            it["dup_score"] = 1.0
            exact_pairs.append((it["n"], best_of))
            # 同文は基準に採用しない（後続の比較基準に含めない）。
        elif best_status == "near":
            it["dup_status"] = "near"
            it["dup_of"] = best_of
            it["dup_score"] = round(best_score, 3)
            near_pairs.append((it["n"], best_of, round(best_score, 3)))
            kept.append((idx, norm))  # 近似は採用（＝別内容として通しうる）
        else:
            kept.append((idx, norm))

    return {
        "total": len(items),
        "exact": len(exact_pairs),
        "near": len(near_pairs),
        "unique": sum(1 for it in items if it["dup_status"] == "unique"),
        "exact_pairs": exact_pairs,
        "near_pairs": near_pairs,
    }
