from importlib import import_module

from celery import shared_task
from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.utils import timezone

__all__ = ("revoke_user_sessions",)


@shared_task
def revoke_user_sessions(user_ids):
    """Delete every live session belonging to `user_ids` (A17, #101).

    Sessions are not indexed by user: the user id lives inside the signed,
    encoded session_data. So this is one pass over the unexpired sessions --
    ~227k on hncode.edu.vn, ~40 s -- which is why it runs on celery and not
    inside the admin request. SessionStore.delete() clears the cached_db
    cache entry as well as the row. Returns the number of sessions deleted.
    """
    wanted = {str(user_id) for user_id in user_ids}
    if not wanted:
        return 0
    store = import_module(settings.SESSION_ENGINE).SessionStore
    deleted = 0
    sessions = (
        Session.objects.filter(expire_date__gt=timezone.now())
        .only("session_key", "session_data")
        .iterator(chunk_size=2000)
    )
    for session in sessions:
        try:
            user_id = session.get_decoded().get(SESSION_KEY)
        except Exception:
            continue
        if user_id in wanted:
            store(session.session_key).delete()
            deleted += 1
    return deleted
