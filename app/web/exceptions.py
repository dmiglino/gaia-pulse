"""Control-flow exceptions raised by the web (SSR) layer."""


class OnboardingRequiredError(Exception):
    """Raised while building a page context for a user who never onboarded.

    ``get_template_context`` is the single point every SSR route goes through,
    so raising from there gates the whole app by construction instead of
    relying on each route remembering to check ``onboarding_completed``.
    """
