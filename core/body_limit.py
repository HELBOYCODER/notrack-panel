"""Bounded HTTP body buffering independent of Content-Length and JSON schema."""
from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes: int = 1048576):
        if not 1 <= max_bytes <= 1048576:
            raise ValueError('Request body limit must be between 1 and 1048576 bytes')
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            return await self.app(scope, receive, send)

        async def reject(status, kind, message, **detail):
            response = JSONResponse({'error': {'type': kind, 'message': message, **detail}}, status_code=status)
            await response(scope, receive, send)

        lengths = [value for name, value in scope.get('headers', []) if name.lower() == b'content-length']
        if lengths and (len(lengths) != 1 or not lengths[0].isdigit()):
            return await reject(400, 'invalid_content_length', 'Content-Length must be one non-negative decimal integer.')
        if lengths:
            digits = lengths[0].lstrip(b'0') or b'0'
            ceiling = str(self.max_bytes).encode('ascii')
            if len(digits) > len(ceiling) or (len(digits) == len(ceiling) and digits > ceiling):
                return await reject(413, 'request_too_large', 'Request body exceeds the configured byte limit.', limit_bytes=self.max_bytes)
        encodings = [value.lower() for name, value in scope.get('headers', []) if name.lower() == b'content-encoding']
        if encodings and encodings != [b'identity']:
            return await reject(415, 'unsupported_encoding', 'Compressed request bodies are not supported.')

        # ponytail: bounded in-memory buffering for a single-operator API — upgrade to a streaming parser before accepting larger uploads.
        chunks = []
        total = 0
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            if event['type'] != 'http.request':
                continue
            chunk = event.get('body', b'')
            if len(chunk) > self.max_bytes - total:
                return await reject(413, 'request_too_large', 'Request body exceeds the configured byte limit.', limit_bytes=self.max_bytes)
            if chunk:
                chunks.append(chunk)
                total += len(chunk)
            if not event.get('more_body', False):
                break
        body = b''.join(chunks)
        chunks.clear()
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
