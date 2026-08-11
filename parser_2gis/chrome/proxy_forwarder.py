from __future__ import annotations
import base64, re, socket, threading
from .utils import free_port


class ProxyForwarder:
    """Local CONNECT tunnel adding Basic auth for upstream HTTP proxy."""

    def __init__(self, upstream: str) -> None:
        m = re.match(r'(?:(?P<u>[^:]+):(?P<p>[^@]+)@)?(?P<h>[^:]+):(?P<pt>\d+)', upstream)
        self._h = m.group('h')
        self._pt = int(m.group('pt'))
        self._auth = ''
        if m.group('u'):
            self._auth = 'Basic ' + base64.b64encode(f'{m.group("u")}:{m.group("p")}'.encode()).decode()
        self.local_port = free_port()
        self._s = None

    def start(self) -> str:
        self._s = socket.socket()
        self._s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._s.bind(('127.0.0.1', self.local_port))
        self._s.listen(64)
        threading.Thread(target=self._loop, daemon=True).start()
        return f'http://127.0.0.1:{self.local_port}'

    def _loop(self) -> None:
        while self._s:
            try:
                c, _ = self._s.accept()
            except Exception:
                return
            threading.Thread(target=self._hnd, args=(c,), daemon=True).start()

    def _hnd(self, c) -> None:
        try:
            req = b''
            while b'\r\n\r\n' not in req:
                b = c.recv(4096)
                if not b:
                    return c.close()
                req += b
                if len(req) > 65536:
                    return c.close()
            m = re.match(r'CONNECT ([^ :]+):(\d+)', req.split(b'\r\n', 1)[0].decode('ignore'))
            if not m:
                return c.close()
            h, p = m.group(1), int(m.group(2))
            up = socket.create_connection((self._h, self._pt), timeout=30)
            a = f'Proxy-Authorization: {self._auth}\r\n' if self._auth else ''
            up.sendall(f'CONNECT {h}:{p} HTTP/1.1\r\nHost: {h}:{p}\r\n{a}\r\n'.encode())
            r = b''
            while b'\r\n\r\n' not in r:
                b = up.recv(4096)
                if not b:
                    break
                r += b
            if not r.startswith(b'HTTP/1.1 200') and not r.startswith(b'HTTP/1.0 200'):
                c.close(); up.close(); return
            c.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')

            def pump(a, b):
                try:
                    while True:
                        d = a.recv(65536)
                        if not d:
                            break
                        b.sendall(d)
                except Exception:
                    pass
                finally:
                    try:
                        b.close()
                    except Exception:
                        pass

            t1 = threading.Thread(target=pump, args=(c, up), daemon=True)
            t2 = threading.Thread(target=pump, args=(up, c), daemon=True)
            t1.start(); t2.start(); t1.join(); t2.join()
        except Exception:
            try:
                c.close()
            except Exception:
                pass

    def close(self) -> None:
        try:
            if self._s:
                self._s.close()
        except Exception:
            pass
