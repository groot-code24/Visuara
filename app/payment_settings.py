"""Payment settings checks, with no import from the rest of the app, so `config.production_problems` can use them while
`app.main` is still being imported (see app/payments.py for the payment flow)."""
import os

PROVIDERS = ('sandbox', 'stripe')


def config_problem(env=None):
    """Why the configured provider cannot be used, or '' when it can (or none is configured)."""
    env = os.environ if env is None else env
    name = (env.get('CC_PAYMENT_PROVIDER') or '').strip().lower()
    if not name: return ''
    if name not in PROVIDERS: return f'CC_PAYMENT_PROVIDER must be one of {", ".join(PROVIDERS)}'
    if len(env.get('CC_PAYMENT_WEBHOOK_SECRET') or '') < 16: return 'CC_PAYMENT_WEBHOOK_SECRET must be set (at least 16 characters) to verify webhooks'
    if name == 'stripe':
        key = env.get('CC_STRIPE_SECRET_KEY') or ''
        if not key.startswith(('sk_test_', 'sk_live_')): return 'CC_STRIPE_SECRET_KEY must be a Stripe secret key (sk_test_... or sk_live_...)'
        if key.startswith('sk_live_') and env.get('CC_PAYMENT_LIVE') != '1':
            return 'a live Stripe key needs the owner\'s explicit authorisation: CC_PAYMENT_LIVE=1'
    return ''


def mode(env=None):
    env = os.environ if env is None else env
    return 'live' if (env.get('CC_PAYMENT_PROVIDER') or '').strip().lower() == 'stripe' and (env.get('CC_STRIPE_SECRET_KEY') or '').startswith('sk_live_') else 'test'
