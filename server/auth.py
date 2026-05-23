from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from flask_login import login_user, logout_user, current_user, login_required

from models import User, db

from datetime import datetime

from urllib.parse import urlparse

from utils import is_valid_password

from rate_limit import _check_and_consume, _client_ip, _settings, _refuse

from audit import audit

from user_accounts import (

    find_user_for_login,

    username_taken,

    email_taken,

    login_session_domain_id,

)



auth_bp = Blueprint('auth', __name__)





@auth_bp.route('/')

def index():

    if current_user.is_authenticated:

        return redirect(url_for('main.dashboard'))

    return redirect(url_for('auth.login'))





@auth_bp.route('/login', methods=['GET', 'POST'])

def login():

    if current_user.is_authenticated:

        return redirect(url_for('main.dashboard'))



    if request.method == 'POST':

        attempted_username = (request.form.get('username') or '').strip()[:120]

        tenant_slug = (request.form.get('tenant_slug') or '').strip()[:64]

        cfg = _settings()

        if cfg.get('ratelimit.enabled', True):

            limit = cfg.get('ratelimit.login_per_min', 5) or 5

            key = f'ip:{_client_ip()}:login'

            ok, retry = _check_and_consume(key, limit, 60)

            if not ok:

                try:

                    import alerts as _alerts

                    import settings as _alert_settings

                    if bool(_alert_settings.effective_value('alerts.bad_login_enabled')):

                        ip = _client_ip()

                        _alerts.notify_event(

                            'login_rate_limited',

                            '[AISignX] Login attempts blocked',

                            f'Login attempts from {ip} were blocked by the rate limiter.\n\n'

                            f'Username attempted: {attempted_username or "(blank)"}\n'

                            f'Retry after       : {int(retry) + 1} seconds\n',

                            target_type='login',

                            target_id=ip,

                            throttle_key=f'login-rate-limited:{ip}',

                            payload={

                                'ip_address': ip,

                                'username': attempted_username,

                                'retry_after_seconds': int(retry) + 1,

                                'limit_per_minute': limit,

                            },

                        )

                except Exception:

                    pass

                if request.accept_mimetypes.best == 'application/json':

                    return _refuse(key, retry)

                flash(f'Too many login attempts. Try again in {int(retry)+1} seconds.',

                      'danger')

                return redirect(url_for('auth.login'))



    if request.method == 'POST':

        username = (request.form.get('username') or '').strip()

        password = request.form.get('password') or ''

        remember = 'remember' in request.form

        tenant_slug = (request.form.get('tenant_slug') or '').strip()



        user, err = find_user_for_login(tenant_slug, username)



        if err == 'invalid_tenant':

            audit('security.login_failed', target_type='login',

                  target_id=(username or '')[:64],

                  payload={'username': username, 'reason': 'invalid_tenant',

                           'tenant_slug': tenant_slug[:64]})

            flash('Invalid organization code. Check the code from your administrator.', 'danger')

            return redirect(url_for('auth.login'))



        if user is None or not user.check_password(password):

            audit('security.login_failed', target_type='login',

                  target_id=(username or '')[:64],

                  payload={'username': username, 'reason': 'invalid_credentials',

                           'tenant_slug': tenant_slug[:64] if tenant_slug else None})

            flash('Invalid organization code, username, or password', 'danger')

            return redirect(url_for('auth.login'))



        if not user.active:

            audit('security.login_failed', target_type='login',

                  target_id=str(user.id),

                  payload={'username': username, 'reason': 'inactive_account'})

            flash('Your account has been deactivated. Please contact an administrator.', 'danger')

            return redirect(url_for('auth.login'))



        if getattr(user, 'is_service_account', False):

            audit('security.login_failed', target_type='login',

                  target_id=str(user.id),

                  payload={'username': username, 'reason': 'service_account_no_web_login'})

            flash('This account is API-only and cannot sign in to the web console. Use an API token instead.', 'danger')

            return redirect(url_for('auth.login'))



        user.last_login = datetime.now()

        db.session.commit()



        did = login_session_domain_id(user)

        if did is not None:

            session['current_domain_id'] = did



        login_user(user, remember=remember)



        audit('security.login_success', target_type='user', target_id=str(user.id),

              payload={'username': username,

                         'home_domain_id': user.home_domain_id,

                         'tenant_slug': tenant_slug[:64] if tenant_slug else None},

              domain_id=did)



        next_page = request.args.get('next')

        if not next_page or urlparse(next_page).netloc != '':

            next_page = url_for('main.dashboard')



        return redirect(next_page)



    return render_template('login.html')





@auth_bp.route('/logout')

def logout():

    logout_user()

    return redirect(url_for('auth.login'))





@auth_bp.route('/profile', methods=['GET', 'POST'])

@login_required

def profile():

    """User profile page"""

    if request.method == 'POST':

        action = request.args.get('action', '')



        if action == 'update_profile':

            home_did = current_user.home_domain_id



            if 'username' in request.form and request.form.get('username') != current_user.username:

                new_name = (request.form.get('username') or '').strip()

                if username_taken(home_did, new_name, exclude_user_id=current_user.id):

                    flash('That username is already used in your organization', 'danger')

                    return redirect(url_for('auth.profile', section='profile'))

                current_user.username = new_name



            if 'email' in request.form and request.form.get('email') != current_user.email:

                new_email = (request.form.get('email') or '').strip()

                if email_taken(home_did, new_email, exclude_user_id=current_user.id):

                    flash('That email is already used in your organization', 'danger')

                    return redirect(url_for('auth.profile', section='profile'))

                current_user.email = new_email



            db.session.commit()

            flash('Profile updated successfully', 'success')

            return redirect(url_for('auth.profile', section='profile'))



        elif action == 'change_password':

            current_password = request.form.get('current_password')

            new_password = request.form.get('new_password')

            confirm_password = request.form.get('confirm_password')



            if not current_user.check_password(current_password):

                flash('Current password is incorrect', 'danger')

                return redirect(url_for('auth.profile', section='password'))



            if new_password != confirm_password:

                flash('New passwords do not match', 'danger')

                return redirect(url_for('auth.profile', section='password'))



            if not is_valid_password(new_password):

                flash('Password does not meet security requirements', 'danger')

                return redirect(url_for('auth.profile', section='password'))



            current_user.set_password(new_password)

            db.session.commit()

            flash('Password changed successfully', 'success')

            return redirect(url_for('auth.profile', section='password'))



    section = request.args.get('section', 'profile')

    if section not in ['profile', 'password']:

        section = 'profile'



    return render_template('profile.html', active_section=section)


