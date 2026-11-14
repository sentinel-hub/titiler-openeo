"""A static HTTP server with range requests, for the vector `load_url` tests.

Run it as a script: `python range_http_server.py <directory>`. It prints its
port on the first line of stdout. It must run in its own process: pyogrio
keeps the GIL while GDAL makes some HTTP requests, so a server thread in the
test process can't answer them.
"""

import io
import os
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class RangeHandler(SimpleHTTPRequestHandler):
    """Serve files, with `Range: bytes=start-end` support (GDAL /vsicurl/)."""

    def log_message(self, *args):  # noqa: D102
        pass

    def send_head(self):  # noqa: D102
        path = self.translate_path(self.path.split("?")[0])
        if not os.path.isfile(path):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        with open(path, "rb") as f:
            data = f.read()
        match = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if match:
            start = int(match.group(1))
            end = min(int(match.group(2) or len(data) - 1), len(data) - 1)
            body = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return io.BytesIO(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(RangeHandler, directory=sys.argv[1])
    )
    print(server.server_port, flush=True)
    server.serve_forever()
