from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length


class LoginForm(FlaskForm):
    email = StringField("メールアドレス", validators=[DataRequired(), Email()])
    password = PasswordField("パスワード", validators=[DataRequired()])
    submit = SubmitField("ログイン")


class RegisterForm(FlaskForm):
    display_name = StringField("お名前（表示名）", validators=[DataRequired(), Length(max=80)])
    email = StringField("メールアドレス", validators=[DataRequired(), Email()])
    password = PasswordField("パスワード", validators=[DataRequired(), Length(min=8)])
    password2 = PasswordField("パスワード（確認）", validators=[DataRequired(), EqualTo("password", message="パスワードが一致しません")])
    submit = SubmitField("アカウントを作成")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("現在のパスワード", validators=[DataRequired()])
    new_password = PasswordField("新しいパスワード", validators=[DataRequired(), Length(min=8)])
    new_password2 = PasswordField("新しいパスワード（確認）", validators=[DataRequired(), EqualTo("new_password", message="パスワードが一致しません")])
    submit = SubmitField("パスワードを変更する")
