"""Template context shared by every page."""


def pending_download(request):
    """
    A file the previous action prepared (the report of an evaluation just finished): the next page offers it and
    starts its download once (forms.js). Taken out of the session here, so it is offered a single time.
    """
    session = getattr(request, 'session', None)
    url = session.pop('pending_download', None) if session is not None else None
    return {'pending_download': url if isinstance(url, str) and url.startswith('/') else None}
