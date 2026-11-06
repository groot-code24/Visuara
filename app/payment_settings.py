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


# Plan subscriptions through Lemon Squeezy (app/billing.py). (plan, interval) -> the setting holding its variant id.
BILLING_VARIANTS = {('pro', 'monthly'): 'LEMONSQUEEZY_VARIANT_PRO_MONTHLY', ('pro', 'annual'): 'LEMONSQUEEZY_VARIANT_PRO_ANNUAL',
                    ('team', 'monthly'): 'LEMONSQUEEZY_VARIANT_AGENCY_MONTHLY', ('team', 'annual'): 'LEMONSQUEEZY_VARIANT_AGENCY_ANNUAL'}


def billing_problem(env=None):
    """Why Lemon Squeezy checkouts cannot work, or '' when they can (or billing is not configured at all)."""
    env = os.environ if env is None else env
    if not env.get('LEMONSQUEEZY_API_KEY'): return ''
    if not (env.get('LEMONSQUEEZY_STORE_ID') or '').strip().isdigit(): return 'LEMONSQUEEZY_STORE_ID must be the numeric store id (Lemon Squeezy -> Settings -> Stores)'
    if len(env.get('LEMONSQUEEZY_WEBHOOK_SECRET') or '') < 16: return 'LEMONSQUEEZY_WEBHOOK_SECRET must be set (at least 16 characters, the signing secret of the webhook)'
    bad = [k for k in BILLING_VARIANTS.values() if env.get(k) and not env[k].strip().isdigit()]
    if bad: return f'{", ".join(bad)} must be numeric variant ids'
    if not any(env.get(k) for k in BILLING_VARIANTS.values()): return f'set at least one plan variant: {", ".join(BILLING_VARIANTS.values())}'
    return ''


def billing_mode(env=None):
    """'live' only with the owner's explicit authorisation for real money; otherwise checkouts are test mode."""
    env = os.environ if env is None else env
    return 'live' if env.get('CC_PAYMENT_LIVE') == '1' else 'test'
