# NOTE: Render では render.yaml の startCommand が優先される。この Procfile は
# render.yaml を使わない経路（Heroku 互換 / ローカル foreman 等）向けの整合コピー。
# 起動前に ensure-schema を実行（失敗しても || true で起動を止めない）。
web: python -m flask --app wsgi ensure-schema || true; gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
