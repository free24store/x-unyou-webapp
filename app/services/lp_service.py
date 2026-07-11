"""
LP・セールスレター・LINEステップ生成サービス

テンプレートファースト設計：
  - ANTHROPIC_API_KEY 未設定でもプロフィールデータから高品質なコンテンツを生成
  - API キーが設定されている場合は Claude でコピーをさらに磨く（オプション）
"""
from .claude_client import call_claude


def _ai_enhance(prompt, fallback: str, max_tokens: int = 500) -> str:
    """Claude が使える場合に強化し、使えなければ fallback をそのまま返す"""
    result = call_claude(prompt, max_tokens=max_tokens)
    return result.strip() if result else fallback


def _pain_points_for_genre(genre: str, who: str) -> str:
    """ジャンルから典型的な悩みリストを返す"""
    defaults = [
        f"何を発信すればいいかわからず投稿が止まってしまう",
        f"フォロワーが全然増えず、努力が報われない気がする",
        f"継続できず、途中で心が折れてしまう",
    ]
    if "X" in genre or "ツイッター" in genre or "Twitter" in genre:
        defaults = [
            "投稿しても全然バズらず、インプレッションが一桁のまま",
            "何を書けばフォロワーが増えるのか、まったく見当がつかない",
            "毎日投稿しているのに3ヶ月経ってもフォロワー100人を超えられない",
        ]
    elif "コーチング" in genre or "コンサル" in genre:
        defaults = [
            "SNSで発信しているのに問い合わせが来ない",
            "自分の強みをどう言語化すればいいかわからない",
            "競合と差別化できず、値下げ競争に巻き込まれている",
        ]
    elif "副業" in genre or "稼ぐ" in genre:
        defaults = [
            "何から始めればいいかわからず、情報収集で終わってしまう",
            "時間をかけているのに全く収益につながらない",
            "怪しいと思われそうで、周りに言えずひとりで悩んでいる",
        ]
    items = "".join(f"<li>{p}</li>" for p in defaults)
    return items


def generate_lp_html(profile, line_url: str) -> str:
    """プロフィールデータからLPのHTMLを生成（API不要）"""
    genre = profile.genre or "X運用"
    who   = profile.who   or "フォロワーを増やしたい方"
    what  = profile.what  or "あなたのSNS運用"
    how   = profile.how   or "独自のメソッド"
    name  = profile.display_name or ""
    achievement = profile.achievement or f"{genre}で成果を出した実績多数"

    # ── テンプレートベースのコピー（API不要） ──
    headline_template = f"{who}が{what}を{how}で実現する"
    pain_items = _pain_points_for_genre(genre, who)
    solution_template = (
        f"だから、{what}があります。"
        f"{name + 'の' if name else ''}{how}で、{who}が抱える悩みを根本から解決します。"
    )

    # ── AI がある場合のみコピーを強化 ──
    headline = _ai_enhance(
        f"X（Twitter）{genre}専門家のランディングページのキャッチコピーを1文（30〜40字）で。"
        f"ターゲット：{who}。提供価値：{what}。断言口調。出力は1文のみ。",
        fallback=headline_template
    )
    solution = _ai_enhance(
        f"「{what}があります」という書き出しで始まる解決策の文章を100字以内で。"
        f"発信テーマ：{genre}、方法：{how}",
        fallback=solution_template
    )
    voice1 = _ai_enhance(
        f"{genre}サービスの顧客の声を1件、イニシャル（例：Aさん・30代）で60字以内で。",
        fallback=f"始めて3ヶ月でフォロワーが500人増えました！（Aさん・30代）"
    )
    voice2 = _ai_enhance(
        f"{genre}サービスの別の顧客の声を1件、別の年代・職業で60字以内で。",
        fallback=f"投稿の方向性が定まり、毎日迷わず発信できるようになりました。（Bさん・40代・主婦）"
    )
    faq1a = _ai_enhance(
        f"「{genre}初心者でも大丈夫ですか？」への回答を40字以内で安心感ある文体で。",
        fallback="はい、全くの初心者でも丁寧にサポートします。まずはお気軽にご相談ください。"
    )
    faq2a = _ai_enhance(
        f"「成果が出るまでどのくらいかかりますか？」への回答を50字以内で。ジャンル：{genre}",
        fallback="個人差はありますが、3ヶ月を目安に変化を実感される方が多いです。"
    )

    return f"""
<section class="text-center py-5" style="background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:#fff;min-height:60vh;display:flex;align-items:center">
  <div class="container">
    <p class="small text-warning mb-2 fw-bold">— {who} へ —</p>
    <h1 class="display-6 fw-bold mb-4" style="line-height:1.4">{headline}</h1>
    <p class="lead mb-4 opacity-75">まずは公式LINEに登録して、無料特典を受け取ってください</p>
    <a href="{line_url}" target="_blank"
       class="btn btn-success btn-lg px-5 py-3 fw-bold shadow"
       style="font-size:1.15rem;border-radius:50px">
      📲 LINEに登録する（無料）
    </a>
    <p class="mt-3 small opacity-50">※ しつこい営業は一切しません</p>
  </div>
</section>

<section class="py-5 bg-light">
  <div class="container" style="max-width:720px">
    <h2 class="h3 text-center mb-4">こんな悩みはありませんか？</h2>
    <ul class="list-group list-group-flush fs-5 shadow-sm">
      {pain_items}
    </ul>
    <p class="text-center mt-4 text-muted">もし1つでも当てはまるなら、続きを読んでください。</p>
  </div>
</section>

<section class="py-5">
  <div class="container" style="max-width:720px">
    <h2 class="h3 text-center mb-4">解決策があります</h2>
    <p class="fs-5 text-center mb-4">{solution}</p>
    <div class="text-center">
      <a href="{line_url}" target="_blank" class="btn btn-outline-success px-4 py-2">
        📲 まず話を聞いてみる（無料）
      </a>
    </div>
  </div>
</section>

<section class="py-5" style="background:#f8f9ff">
  <div class="container" style="max-width:720px">
    <h2 class="h3 text-center mb-4">実績・信頼</h2>
    <p class="fs-5 text-center">{achievement}</p>
  </div>
</section>

<section class="py-5 bg-white">
  <div class="container" style="max-width:720px">
    <h2 class="h3 text-center mb-4">お客様の声</h2>
    <div class="card mb-3 shadow-sm border-0">
      <div class="card-body">
        <p class="mb-0 fst-italic">「{voice1}」</p>
      </div>
    </div>
    <div class="card shadow-sm border-0">
      <div class="card-body">
        <p class="mb-0 fst-italic">「{voice2}」</p>
      </div>
    </div>
  </div>
</section>

<section class="py-5 bg-light">
  <div class="container" style="max-width:720px">
    <h2 class="h3 text-center mb-4">よくある質問</h2>
    <div class="accordion shadow-sm" id="faqAccordion">
      <div class="accordion-item border-0 mb-2">
        <h3 class="accordion-header">
          <button class="accordion-button collapsed rounded" type="button" data-bs-toggle="collapse" data-bs-target="#faq1">
            Q. {genre}初心者でも大丈夫ですか？
          </button>
        </h3>
        <div id="faq1" class="accordion-collapse collapse" data-bs-parent="#faqAccordion">
          <div class="accordion-body">{faq1a}</div>
        </div>
      </div>
      <div class="accordion-item border-0">
        <h3 class="accordion-header">
          <button class="accordion-button collapsed rounded" type="button" data-bs-toggle="collapse" data-bs-target="#faq2">
            Q. 成果が出るまでどのくらいかかりますか？
          </button>
        </h3>
        <div id="faq2" class="accordion-collapse collapse" data-bs-parent="#faqAccordion">
          <div class="accordion-body">{faq2a}</div>
        </div>
      </div>
    </div>
  </div>
</section>

<section class="text-center py-5" style="background:linear-gradient(135deg,#0f0c29,#302b63);color:#fff">
  <div class="container" style="max-width:600px">
    <h2 class="h3 mb-3">まずは無料で相談してみてください</h2>
    <p class="mb-4 opacity-75">登録するだけ。費用は一切かかりません。</p>
    <a href="{line_url}" target="_blank"
       class="btn btn-success btn-lg px-5 py-3 fw-bold shadow"
       style="font-size:1.15rem;border-radius:50px">
      📲 LINEに登録する（無料）
    </a>
  </div>
</section>
"""


def generate_sales_letter_html(profile, product_name: str, price_jpy: int,
                                benefits: str, deadline: str, stripe_link: str,
                                contact_email: str, contact_phone: str) -> str:
    """セールスレターのHTMLを生成（API不要）"""
    genre = profile.genre or "X運用"
    who   = profile.who   or "フォロワーを増やしたい方"
    name  = profile.display_name or ""

    # テンプレートベースのコピー
    headline_tmpl = f"今だけ公開：{who}が{genre}で変わる完全ロードマップ「{product_name}」"
    problem_tmpl  = (
        f"Xを頑張っているのに全然伸びない——そんな状況、ずっと続けていませんか？"
        f"正しい方法を知らないだけで、{who}の努力は今も無駄になっています。"
    )
    offer_tmpl    = f"「{product_name}」は、{genre}で結果を出すための完全ロードマップです。"
    urgency_tmpl  = f"※ {deadline}で締め切ります。お早めにお申し込みください。"

    headline = _ai_enhance(
        f"「{product_name}」（{price_jpy:,}円）のセールスレターのヘッドラインを1文で（40〜50字）。"
        f"ターゲット：{who}。断言型コピー。出力は1文のみ。",
        fallback=headline_tmpl
    )
    problem = _ai_enhance(
        f"X{genre}プログラムのセールスレター用「問題提起」文を100字以内で。"
        f"ターゲット{who}の悩みに共感するトーン。",
        fallback=problem_tmpl
    )
    offer = _ai_enhance(
        f"「{product_name}」の内容・魅力を80字以内で。特典：{benefits[:50]}",
        fallback=offer_tmpl
    )
    urgency = _ai_enhance(
        f"「{deadline}」を使った申し込みを急かす一文を30字以内で。",
        fallback=urgency_tmpl
    )

    benefit_items = "".join(
        f'<li class="list-group-item"><span class="text-success fw-bold me-2">✓</span>{b.strip()}</li>'
        for b in benefits.splitlines() if b.strip()
    ) or '<li class="list-group-item"><span class="text-success fw-bold me-2">✓</span>専属サポート付き</li>'

    payment_btn = (
        f'<a href="{stripe_link}" target="_blank" '
        f'class="btn btn-danger btn-lg px-5 py-3 fw-bold shadow" style="font-size:1.1rem;border-radius:50px">'
        f'今すぐ申し込む（¥{price_jpy:,}）</a>'
        if stripe_link else
        f'<p class="fs-4 fw-bold text-danger mb-2">価格：¥{price_jpy:,}（税込）</p>'
        f'<p class="text-muted small">お申し込みはLINEまたはお問い合わせフォームから</p>'
    )

    contact_section = ""
    if contact_email or contact_phone:
        ph_line = (f'<p class="mb-1">📞 <a href="tel:{contact_phone}">{contact_phone}</a></p>'
                   if contact_phone else "")
        em_line = (f'<p class="mb-3">✉️ <a href="mailto:{contact_email}">{contact_email}</a></p>'
                   if contact_email else "")
        contact_section = f"""
<section class="py-5 bg-light">
  <div class="container" style="max-width:640px">
    <h3 class="h5 text-center mb-4">📩 お問い合わせ・ご相談</h3>
    {ph_line}{em_line}
    <form method="POST" action="" class="contact-form">
      <input type="hidden" name="source" value="sales_letter">
      <div class="mb-2"><input class="form-control" name="name" placeholder="お名前" required></div>
      <div class="mb-2"><input class="form-control" name="email" type="email" placeholder="メールアドレス" required></div>
      <div class="mb-2"><input class="form-control" name="phone" placeholder="電話番号（任意）"></div>
      <div class="mb-2"><textarea class="form-control" name="body" rows="3" placeholder="ご質問・ご相談内容" required></textarea></div>
      <button type="submit" class="btn btn-outline-primary w-100">送信する</button>
    </form>
  </div>
</section>"""

    return f"""
<section class="text-center py-5" style="background:linear-gradient(135deg,#1a1a2e,#16213e,#0f3460);color:#fff;min-height:50vh;display:flex;align-items:center">
  <div class="container" style="max-width:720px">
    <p class="small text-warning mb-2 fw-bold">— {who} のための —</p>
    <h1 class="h2 fw-bold mb-4" style="line-height:1.4">{headline}</h1>
    <p class="lead opacity-75">{problem}</p>
  </div>
</section>

<section class="py-5">
  <div class="container" style="max-width:720px">
    <h2 class="h3 mb-3">このプログラムで得られること</h2>
    <p class="mb-4 fs-5">{offer}</p>
    <ul class="list-group list-group-flush">{benefit_items}</ul>
  </div>
</section>

<section class="py-5 text-center" style="background:#fff9f0">
  <div class="container" style="max-width:640px">
    <h2 class="h3 mb-2">お申し込み</h2>
    <p class="text-muted mb-4">{urgency}</p>
    {payment_btn}
    <p class="mt-3 small text-muted">※ 決済完了後、メールにて詳細をお送りします。</p>
  </div>
</section>

{contact_section}
"""


def generate_line_steps(profile, product_name: str = "", sales_letter_url: str = "") -> list:
    """7日間LINEステップ配信メッセージを生成（API不要）"""
    genre = profile.genre or "X運用"
    name  = profile.display_name or "担当者"
    who   = profile.who or "フォロワーを増やしたい方"
    what  = profile.what or "あなたのサービス"

    sl_url = sales_letter_url or "（URLは後で設定してください）"
    prod   = product_name or what

    templates = [
        {
            "day": 0, "timing": "登録直後",
            "message": (
                f"はじめまして、{name}です！\n\n"
                f"LINE登録ありがとうございます🙏\n\n"
                f"あなたに今すぐ使える\n"
                f"【{genre}実践チェックリスト】\n"
                f"をプレゼントします✨\n\n"
                f"▼ 無料ダウンロードはこちら\n"
                f"（ファイルURL or 画像を送付）\n\n"
                f"これからあなたの{genre}を\n"
                f"一緒に変えていきましょう！\n\n"
                f"質問はいつでもどうぞ😊"
            )
        },
        {
            "day": 1, "timing": "翌日",
            "message": (
                f"こんにちは、{name}です！\n\n"
                f"突然ですが、{who}の方が\n"
                f"一番多く抱えている悩みって\n"
                f"何だと思いますか？\n\n"
                f"…それは\n"
                f"「何を投稿すればいいかわからない」\n"
                f"なんです。\n\n"
                f"実はこれ、ほとんどの方が\n"
                f"発信の「軸」が決まっていないことが原因です。\n\n"
                f"軸さえ決まれば、迷いは9割なくなります。\n\n"
                f"明日はその具体的な決め方をお伝えしますね👍"
            )
        },
        {
            "day": 3, "timing": "3日後",
            "message": (
                f"こんにちは😊\n\n"
                f"今日は実際の事例をシェアします。\n\n"
                f"Aさん（フォロワー50人からスタート）\n"
                f"→ 3ヶ月でフォロワー1,000人達成✨\n"
                f"→ そこから月3件のDM相談が来るように\n\n"
                f"Bさん（何を投稿するか全く決まらない状態から）\n"
                f"→ 発信テーマを1つに絞った翌週から\n"
                f"　インプレッションが3倍に\n\n"
                f"お二人の共通点は\n"
                f"「型を持って動いた」こと。\n\n"
                f"型さえあれば、{genre}は必ず変わります。\n\n"
                f"あなたにも同じことができます💪"
            )
        },
        {
            "day": 5, "timing": "5日後",
            "message": (
                f"こんにちは！\n\n"
                f"「{genre}をちゃんとやろうと思うけど\n"
                f"　正直、続けられるか不安…」\n\n"
                f"そう思っていませんか？\n\n"
                f"その不安、すごくわかります。\n\n"
                f"でも実は、続けられない理由の9割は\n"
                f"「仕組みがないから」なんです。\n\n"
                f"仕組みを作れば、モチベーションに関係なく\n"
                f"自然と続けられるようになります。\n\n"
                f"明日、その仕組みの作り方をお伝えします😊"
            )
        },
        {
            "day": 6, "timing": "6日後",
            "message": (
                f"こんにちは！\n\n"
                f"明日で7日間のステップ配信が終わります。\n\n"
                f"この1週間、あなたの{genre}への向き合い方が\n"
                f"少し変わっていたら嬉しいです😊\n\n"
                f"実は明日、とっておきのご案内があります。\n\n"
                f"「本気で{genre}を変えたい」と思っている方に\n"
                f"向けた、限定のご案内です。\n\n"
                f"明日のメッセージを楽しみにしていてください！"
            )
        },
        {
            "day": 7, "timing": "7日後",
            "message": (
                f"こんにちは！\n\n"
                f"7日間お付き合いいただき\n"
                f"ありがとうございました🙏\n\n"
                f"実は今日、{who}向けの\n"
                f"「{prod}」をご案内します。\n\n"
                f"これまでの7日間でお伝えしてきた内容を\n"
                f"さらに深く、マンツーマンでサポートする\n"
                f"プログラムです。\n\n"
                f"▼ 詳細はこちら\n"
                f"{sl_url}\n\n"
                f"ご質問はこのLINEにどうぞ😊\n"
                f"一緒に頑張りましょう！"
            )
        },
    ]

    # AI がある場合、各メッセージを少し磨く（任意）
    result = []
    for tmpl in templates:
        enhanced = _ai_enhance(
            f"LINEステップ配信メッセージを改善してください。\n"
            f"配信タイミング：{tmpl['timing']}\n"
            f"ジャンル：{genre}、担当者：{name}、ターゲット：{who}\n"
            f"元のメッセージ：\n{tmpl['message']}\n\n"
            f"条件：LINEらしい改行、160〜200字、フレンドリーな敬語。"
            f"出力はメッセージ本文のみ。",
            fallback=tmpl["message"],
            max_tokens=400,
        )
        result.append({"day": tmpl["day"], "timing": tmpl["timing"], "message": enhanced})

    return result
