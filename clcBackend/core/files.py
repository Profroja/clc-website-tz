"""Serving uploaded files that are meant to be public (package and media pictures and videos)."""
import mimetypes
import os
import re
from urllib.parse import quote

from django.core import signing

from django.http import HttpResponse, StreamingHttpResponse


def read_image(field):
    """Images are at most 5 MB: read them and release the file at once (Windows will not delete an open file)."""
    with field.open('rb') as f:
        data = f.read()
    return HttpResponse(data, content_type=mimetypes.guess_type(field.name)[0] or 'application/octet-stream')


def range_response(request, path, default_type='video/mp4'):
    """Stream a file with HTTP Range support, so browsers can seek and start playing at once."""
    size = os.path.getsize(path)
    start, end, status = 0, size - 1, 200
    m = re.match(r'bytes=(\d*)-(\d*)$', request.headers.get('Range', '').strip())
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:  # "bytes=-500": the last 500 bytes
            start = max(size - int(m.group(2)), 0)
        if start > end or start >= size:
            resp = HttpResponse(status=416)
            resp['Content-Range'] = f'bytes */{size}'
            return resp
        status = 206

    def stream(remaining=end - start + 1):
        with open(path, 'rb') as f:  # opened per request and closed when the stream ends
            f.seek(start)
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    resp = StreamingHttpResponse(stream(), status=status, content_type=mimetypes.guess_type(path)[0] or default_type)
    resp['Accept-Ranges'] = 'bytes'
    resp['Content-Length'] = str(end - start + 1)
    if status == 206:
        resp['Content-Range'] = f'bytes {start}-{end}/{size}'
    return resp


# Hidden items are not public, but the admin portal still needs to show their pictures and videos in <img>/<video>
# tags, which cannot send a login token. The admin's responses therefore carry a short-lived signed link.
_signer = signing.TimestampSigner(salt='public-media-preview')
PREVIEW_SECONDS = 24 * 3600


def preview_query(obj_id):
    return '?sig=' + quote(_signer.sign(str(obj_id)), safe='')


def preview_ok(obj_id, token):
    try:
        return _signer.unsign(token or '', max_age=PREVIEW_SECONDS) == str(obj_id)
    except signing.BadSignature:
        return False
