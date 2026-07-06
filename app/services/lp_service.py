"""ランディングページ・セールスレター・LINEステップ生成サービス"""
import json
from .claude_client import call_claude


def _ask(prompt, fallback=""):
    result = call_claude(prompt, max_tokens=800)
    return result.strip() if result else fallback


def generate_lp_html(profile, line_url, extra=None):
    """プロフィール情報からLPのHTMLを生成して返す"""
    extra = extra or {}
    who = profile.who or "〇〇で悩む方"
    what = profile.what or "あなたのサービス"
    how = profile.how or "独自の方法"
    genre = profile.genre or "X運用"
    name = profile.display_name or "担当者"
    achievement = profile.achievement or ""

    headline = _ask(
        f"X（Twitter）運用の{genre}コーチングを提供している{name}のランディングページ用の"
        f"キャッチコピーを1文（30〜40字）で作ってください。"
        f"ターゲット：{who}。提供価値：{what}。文体：力強く断言する日本語。出力は1文のみ。",
        fallback=f"{who}のための{genre}完全サポート"
    )

    pain_copy = _ask(
        f"X（Twitter）{genre}の悩みを持つ{who}に向けて、"
        f"「こんな悩みはありませんか？」のリスト形式で3項目書いてください。"
        f"箇条書き（・）で、それぞれ20〜30字。",
        fallback="・フォロワーがなかなか増えない\n・何を投稿すればいいかわからない\n・継続が難しく諦めかけている"
    )

    solution_copy = _ask(
        f"{name}が{who}に対して{how}で提供するサービスの魅力を"
        f"「だから、{what}があります」という書き出しで100字以内で説明してください。",
        fallback=f"だから、{what}があります。{how}で、あなたのX運用を根本から変えます。"
    )

    achievement_copy = achievement or f"{genre}で成果を出した実績多数"

    voice1 = _ask(
        f"X{genre}サービスの顧客の声（お客様の声・推薦文）を1件、"
        f"名前はイニシャル（例：Aさん・30代・会社員）で60字以内でリアルに書いてください。",
        fallback="始めて3ヶ月でフォロワーが500人増えました！投稿の型を教えてもらったおかげです。（Aさん・30代）"
    )
    voice2 = _ask(
        f"X{genre}サービスの別の顧客の声を1件、別の職業・年代で60字以内で書いてください。",
        fallback="何を投稿すればいいかずっと迷っていましたが、方向性が定まりました。（Bさん・40代・主婦）"
    )

    faq1_a = _ask(
        f"「{genre}初心者でも大丈夫ですか？」という質問への安心感ある回答を40字以内で。",
        fallback="はい、全くの初心者の方でも丁寧にサポートします。"
    )
    faq2_a = _ask(
        f"「サポートはどのくらいの期間ですか？」という質問への回答を40字以内で。",
        fallback="基本は3ヶ月のサポートです。ご相談に応じて延長も可能です。"
    )

    sections = _build_lp_sections(
        headline=headline,
        pain_copy=pain_copy,
        solution_copy=solution_copy,
        name=name,
        achievement_copy=achievement_copy,
        voice1=voice1,
        voice2=voice2,
        faq1_a=faq1_a,
        faq2_a=faq2_a,
        line_url=line_url,
        genre=genre,
    )
    return sections


def _build_lp_sections(headline, pain_copy, solution_copy, name,
                        achievement_copy, voice1, voice2, faq1_a, faq2_a,
                        line_url, genre):
    pain_items = "".join(
        f'<li class="list-group-item">{p.lstrip("・").strip()}</li>'
        for p in pain_copy.splitlines() if p.strip()
    )
    return f"""
<section class="text-center py-5" style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff">
  <div class="container">
    <h1 class="display-5 fw-bold mb-3">{headline}</h1>
    <p class="lead mb-4">まずは公式LINEに登録して、無料特典を受け取ってください</p>
    <a href="{line_url}" target="_blank" class="btn btn-success btn-lg px-5 py-3 fw-bold" style="font-size:1.2rem">
      📲 LINEで無料相談する
    </a>
  </div>
</section>

<section class="py-5 bg-light">
  <div class="container" style="max-width:720px">
    <h2 class="h3 text-center mb-4">こんな悩みはありませんか？</h2>
    <ul class="list-group list-group-flush fs-5">
      {pain_items}
    </ul>
  </div>
</section>

<section class="py-5">
  <div class="container" style="max-width:720px">
    <h2 class="h3 text-center mb-3">解決策はここにあります</h2>
    <p class="fs-5 text-center">{solution_copy}</p>
  </div>
</section>

<section class="py-5 bg-light">
  <div class="container" style="max-width:720px">
    <h2 class="h3 text-center mb-3">実績・信頼</h2>
    <p class="fs-5 text-center">{achievement_copy}</p>
  </div>
</section>

<section class="py-5">
  <div class="container" style="max-width:720px">
    <h2 class="h3 text-center mb-4">お客様の声</h2>
    <div class="card mb-3 shadow-sm"><div class="card-body"><p class="mb-0">「{voice1}」</p></div></div>
    <div class="card shadow-sm"><div class="card-body"><p class="mb-0">「{voice2}」</p></div></div>
  </div>
</section>

<section class="py-5 bg-light">
  <div class="container" style="max-width:720px">
    <h2 class="h3 text-center mb-4">よくある質問</h2>
    <div class="accordion" id="faqAccordion">
      <div class="accordion-item">
        <h3 class="accordion-header"><button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#faq1">Q. {genre}初心者でも大丈夫ですか？</button></h3>
        <div id="faq1" class="accordion-collapse collapse" data-bs-parent="#faqAccordion"><div class="accordion-body">{faq1_a}</div></div>
      </div>
      <div class="accordion-item">
        <h3 class="accordion-header"><button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#faq2">Q. サポートはどのくらいの期間ですか？</button></h3>
        <div id="faq2" class="accordion-collapse collapse" data-bs-parent="#faqAccordion"><div class="accordion-body">{faq2_a}</div></div>
      </div>
    </div>
  </div>
</section>

<section class="text-center py-5" style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff">
  <div class="container">
    <h2 class="h3 mb-3">まずは無料で相談してみてください</h2>
    <p class="mb-4">LINEに登録するだけ。費用は一切かかりません。</p>
    <a href="{line_url}" target="_blank" class="btn btn-success btn-lg px-5 py-3 fw-bold" style="font-size:1.2rem">
      📲 LINEで無料相談する（無料）
    </a>
    <p class="mt-3 small opacity-75">※ しつこい営業は一切しません</p>
  </div>
</section>
"""


def generate_sales_letter_html(profile, product_name, price_jpy,
                                 benefits, deadline, stripe_link,
                                 contact_email, contact_phone):
    """セールスレターのHTMLを生成して返す"""
    who = profile.who or "〇〇で悩む方"
    genre = profile.genre or "X運用"
    name = profile.display_name or "担当者"

    headline = _ask(
        f"「{product_name}」というX{genre}プログラム（{price_jpy:,}円）の"
        f"セールスレター用ヘッドラインを1文（40〜50字）で作ってください。"
        f"ターゲット：{who}。出力は1文のみ。",
        fallback=f"今だけ限定公開：{who}がX運用で結果を出すための完全プログラム"
    )

    problem_copy = _ask(
        f"X{genre}プログラムのセールスレター用に「問題提起」の文章を100字以内で書いてください。"
        f"ターゲットの{who}が抱える問題を共感を込めて描写。",
        fallback=f"Xを頑張っているのに全然伸びない——そんな状況、ずっと続けていませんか？正しい方法を知らないだけで、あなたの努力は無駄になっています。"
    )

    offer_copy = _ask(
        f"「{product_name}」の内容・特徴を80字以内で魅力的に説明してください。"
        f"特典・サポート内容：{benefits}",
        fallback=f"{product_name}は、{genre}で結果を出すための完全ロードマップです。{benefits}"
    )

    urgency_copy = _ask(
        f"「{deadline}」という期限・限定条件を使った申し込みを急かす一文を30字以内で。",
        fallback=f"※ {deadline}で締め切ります。お早めにどうぞ。"
    )

    benefits_items = "".join(
        f'<li class="list-group-item"><span class="text-success fw-bold">✓</span> {b.strip()}</li>'
        for b in benefits.splitlines() if b.strip()
    ) or '<li class="list-group-item"><span class="text-success fw-bold">✓</span> 専属サポート付き</li>'

    contact_section = ""
    if contact_email or contact_phone:
        phone_line = f'<p>📞 <a href="tel:{contact_phone}">{contact_phone}</a></p>' if contact_phone else ""
        email_line = f'<p>✉️ <a href="mailto:{contact_email}">{contact_email}</a></p>' if contact_email else ""
        contact_section = f"""
<section class="py-4 bg-light">
  <div class="container" style="max-width:720px">
    <h3 class="h5 text-center mb-3">お問い合わせ</h3>
    {phone_line}{email_line}
    <form method="POST" action="" class="contact-form mt-3">
      <input type="hidden" name="source" value="sales_letter">
      <div class="mb-2"><input class="form-control" name="name" placeholder="お名前" required></div>
      <div class="mb-2"><input class="form-control" name="email" type="email" placeholder="メールアドレス" required></div>
      <div class="mb-2"><input class="form-control" name="phone" placeholder="電話番号（任意）"></div>
      <div class="mb-2"><textarea class="form-control" name="body" rows="3" placeholder="ご質問・ご相談内容" required></textarea></div>
      <button type="submit" class="btn btn-outline-primary w-100">送信する</button>
    </form>
  </div>
</section>"""

    payment_btn = (
        f'<a href="{stripe_link}" target="_blank" class="btn btn-danger btn-lg px-5 py-3 fw-bold" style="font-size:1.1rem">今すぐ申し込む（{price_jpy:,}円）</a>'
        if stripe_link else
        f'<p class="fs-4 fw-bold text-danger">価格：{price_jpy:,}円（税込）</p>'
    )

    return f"""
<section class="text-center py-5" style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff">
  <div class="container" style="max-width:720px">
    <h1 class="h2 fw-bold mb-3">{headline}</h1>
    <p class="lead">{problem_copy}</p>
  </div>
</section>

<section class="py-5">
  <div class="container" style="max-width:720px">
    <h2 class="h3 mb-3">このプログラムで得られること</h2>
    <p class="mb-4">{offer_copy}</p>
    <ul class="list-group list-group-flush fs-5">{benefits_items}</ul>
  </div>
</section>

<section class="py-5 bg-light">
  <div class="container text-center" style="max-width:720px">
    <h2 class="h3 mb-2">価格・お申し込み</h2>
    <p class="text-muted mb-4">{urgency_copy}</p>
    {payment_btn}
    <p class="mt-3 small text-muted">※ 決済完了後、メールにて詳細をお送りします。</p>
  </div>
</section>

{contact_section}
"""


def generate_line_steps(profile, product_name, sales_letter_url=""):
    """7日間LINEステップ配信メッセージを生成して返す"""
    genre = profile.genre or "X運用"
    name = profile.display_name or "担当者"
    who = profile.who or "フォロワーを増やしたい方"

    steps = []
    step_defs = [
        (0,  "登録直後",    f"{name}からの挨拶＋無料特典プレゼント（{genre}で使える実践チェックリスト）"),
        (1,  "翌日",        f"{who}が抱える最大の悩みへの共感と解決の糸口を提示"),
        (3,  "3日後",       f"{genre}で成果を出した実例・事例を具体的に紹介"),
        (5,  "5日後",       f"「なぜ多くの人がX運用に失敗するか？」という原因解説と対策"),
        (6,  "6日後",       f"限定感を演出：明日が締め切り・残り枠わずかのお知らせ"),
        (7,  "7日後",       f"セールスレターへの案内：URL={sales_letter_url or '（セールスレターURL）'}"),
    ]

    for day, timing, context in step_defs:
        msg = _ask(
            f"LINE公式アカウントのステップ配信メッセージを書いてください。\n"
            f"配信タイミング：{timing}\n"
            f"内容：{context}\n"
            f"文体：フレンドリーで親しみやすい敬語。LINEらしく改行を多めに。150〜200字。\n"
            f"出力はメッセージ本文のみ。",
            fallback=f"【{timing}】{context}についてのメッセージです。"
        )
        steps.append({"day": day, "timing": timing, "message": msg})

    return steps
