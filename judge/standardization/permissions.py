def _authed(user):
    return bool(user) and user.is_authenticated


def can_view_standardization(user):
    return _authed(user) and (
        user.is_superuser or user.has_perm("judge.view_standardizationproposal")
    )


def can_review_standardization(user):
    return _authed(user) and (
        user.is_superuser or user.has_perm("judge.review_standardization")
    )


def can_apply_standardization(user):
    return _authed(user) and (
        user.is_superuser or user.has_perm("judge.apply_standardization")
    )


def can_view_standardization_cost(user):
    return _authed(user) and (
        user.is_superuser or user.has_perm("judge.view_standardization_cost")
    )


def can_view_problem_source(user, problem):
    """Private statement/solution content needs problem edit rights or the view perm."""
    if not _authed(user):
        return False
    if user.is_superuser or problem.is_editable_by(user):
        return True
    return user.has_perm("judge.view_standardizationproposal")
