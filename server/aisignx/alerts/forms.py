from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import InputRequired, Length, NumberRange, Optional, ValidationError

from .mailer import SECURITIES, parse_recipients, valid_address


class ActionForm(FlaskForm):
    """Empty form used only for its CSRF token."""


class AlertSettingsForm(FlaskForm):
    email_enabled = BooleanField("Send alert e-mails")
    email_to = TextAreaField("Recipients (comma-separated)",
                             validators=[Optional(), Length(max=2000)])
    smtp_host = StringField("SMTP server", validators=[Optional(), Length(max=200)])
    smtp_port = IntegerField("Port", validators=[InputRequired(), NumberRange(1, 65535)])
    smtp_security = SelectField("Security", choices=[
        SelectChoice("starttls", "STARTTLS (usually port 587)"),
        SelectChoice("ssl", "SSL/TLS (usually port 465)"),
        SelectChoice("none", "None (trusted local relay only)")])
    smtp_username = StringField("User name", validators=[Optional(), Length(max=200)])
    smtp_from = StringField("From address", validators=[Optional(), Length(max=200)])
    offline_minutes = IntegerField("Display offline after (minutes)",
                                   validators=[InputRequired(), NumberRange(1, 7 * 24 * 60)])
    disk_min_free_gb = IntegerField("Low disk below (GB free)",
                                    validators=[InputRequired(), NumberRange(0, 100000)])
    disk_min_free_pct = IntegerField("or below (% free)",
                                     validators=[InputRequired(), NumberRange(0, 99)])
    login_burst = IntegerField("Failed sign-ins in 10 minutes (0 = off)",
                               validators=[InputRequired(), NumberRange(0, 100000)])
    retention_days = IntegerField("Keep resolved alerts (days, 0 = forever)",
                                  validators=[InputRequired(), NumberRange(0, 3650)])

    def validate_smtp_security(self, field) -> None:
        if field.data not in SECURITIES:
            raise ValidationError("Choose a security mode.")

    def validate_smtp_host(self, field) -> None:
        if field.data and any(c.isspace() for c in field.data.strip()):
            raise ValidationError("A host name cannot contain spaces.")

    def validate_smtp_from(self, field) -> None:
        if field.data and not valid_address(field.data.strip()):
            raise ValidationError("Enter one e-mail address.")

    def validate_email_to(self, field) -> None:
        bad = [a for a in parse_recipients(field.data) if not valid_address(a)]
        if bad:
            raise ValidationError("Not an e-mail address: " + ", ".join(bad[:3]))

    def validate_email_enabled(self, field) -> None:
        if field.data and not (self.smtp_host.data and self.smtp_from.data
                               and parse_recipients(self.email_to.data)):
            raise ValidationError("E-mail needs an SMTP server, a from address and "
                                  "at least one recipient.")
