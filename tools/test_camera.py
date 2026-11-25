#!/usr/bin/env python3
"""A fake RTSP camera, for testing SentryView without real hardware.

Serves an H.264 test pattern over RTSP (RTP/AVP/TCP interleaved), which is the
same transport real IP cameras use. Point SentryView at the printed URL.

    python3 tools/test_camera.py --count 2

Only the RTSP verbs a client needs are implemented: OPTIONS, DESCRIBE, SETUP,
PLAY, TEARDOWN and the GET_PARAMETER keepalive.
"""
from __future__ import annotations

import argparse
import os
import random
import socket
import socketserver
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sentryview.paths import find_ffmpeg  # noqa: E402

MTU = 1400
PAYLOAD_TYPE = 96
CLOCK = 90000
FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
OPTS = argparse.Namespace()


# --------------------------------------------------------------------- encoder
def _font_file() -> str | None:
    return next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)


def encoder_command(label: str) -> list[str]:
    """ffmpeg producing a labelled Annex-B H.264 elementary stream on stdout."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")

    source = f"{OPTS.pattern}=size={OPTS.size}:rate={OPTS.fps}"
    filters = []
    font = _font_file()
    if font and not OPTS.no_label:
        text = f"{label}  %{{localtime\\:%Y-%m-%d %H\\\\\\:%M\\\\\\:%S}}"
        filters.append(
            f"drawtext=fontfile='{font}':text='{text}':x=20:y=20:fontsize=28:"
            "fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=10"
        )
    vf = ["-vf", ",".join(filters)] if filters else []

    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-re", "-f", "lavfi", "-i", source,
        *vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-profile:v", "baseline", "-pix_fmt", "yuv420p",
        "-x264-params", f"keyint={OPTS.fps * 2}:min-keyint={OPTS.fps}:scenecut=0:"
                        "repeat-headers=1:sliced-threads=0",
        "-f", "h264", "pipe:1",
    ]


def split_nals(chunk: bytes, carry: bytes) -> tuple[list[bytes], bytes]:
    """Pull complete Annex-B NAL units out of a byte stream.

    Returns the NALs found and whatever tail must be carried into the next read.
    """
    data = carry + chunk
    out: list[bytes] = []
    starts: list[tuple[int, int]] = []       # (payload offset, start-code length)
    i, n = 0, len(data)
    while i < n - 3:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                starts.append((i + 3, 3))
                i += 3
                continue
            if data[i + 2] == 0 and data[i + 3] == 1:
                starts.append((i + 4, 4))
                i += 4
                continue
        i += 1

    if not starts:
        return out, data[-3:] if n > 3 else data

    for idx, (begin, _) in enumerate(starts[:-1]):
        end = starts[idx + 1][0] - starts[idx + 1][1]
        nal = data[begin:end]
        if nal:
            out.append(nal)
    tail_begin, tail_code = starts[-1]
    return out, data[tail_begin - tail_code:]


# -------------------------------------------------------------------- RTSP I/O
class _Reader:
    """Line/exact reads over a socket, tolerating interleaved RTP from the peer."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.buf = b""

    def _fill(self) -> bool:
        data = self.sock.recv(65536)
        if not data:
            return False
        self.buf += data
        return True

    def need(self, count: int) -> bool:
        while len(self.buf) < count:
            if not self._fill():
                return False
        return True

    def take(self, count: int) -> bytes:
        chunk, self.buf = self.buf[:count], self.buf[count:]
        return chunk

    def readline(self) -> bytes | None:
        while b"\r\n" not in self.buf:
            if not self._fill():
                return None
        line, _, self.buf = self.buf.partition(b"\r\n")
        return line


class RtspHandler(socketserver.BaseRequestHandler):
    def setup(self) -> None:
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.reader = _Reader(self.request)
        self.send_lock = threading.Lock()
        self.session_id = f"{random.randint(10**7, 10**8 - 1)}"
        self.rtp_channel = 0
        self.streaming = threading.Event()
        self.closing = threading.Event()
        self.stream_thread: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None
        self.label = "camera"

    # ------------------------------------------------------------ send helpers
    def send_raw(self, data: bytes) -> None:
        with self.send_lock:
            self.request.sendall(data)

    def respond(self, cseq: str, status: str = "200 OK",
                headers: dict | None = None, body: bytes = b"") -> None:
        head = [f"RTSP/1.0 {status}", f"CSeq: {cseq}", "Server: SentryView-TestCam/1.0"]
        for key, value in (headers or {}).items():
            head.append(f"{key}: {value}")
        if body:
            head.append(f"Content-Length: {len(body)}")
        self.send_raw("\r\n".join(head).encode() + b"\r\n\r\n" + body)

    # ----------------------------------------------------------------- request
    def handle(self) -> None:
        try:
            while not self.closing.is_set():
                if not self.reader.need(1):
                    break
                if self.reader.buf[:1] == b"$":          # peer's RTCP; discard
                    if not self.reader.need(4):
                        break
                    header = self.reader.take(4)
                    length = int.from_bytes(header[2:4], "big")
                    if not self.reader.need(length):
                        break
                    self.reader.take(length)
                    continue

                request_line = self.reader.readline()
                if not request_line:
                    break
                headers = {}
                while True:
                    line = self.reader.readline()
                    if line is None:
                        return
                    if not line:
                        break
                    key, _, value = line.decode("utf-8", "replace").partition(":")
                    headers[key.strip().lower()] = value.strip()
                body_len = int(headers.get("content-length", "0") or 0)
                if body_len and self.reader.need(body_len):
                    self.reader.take(body_len)

                self.dispatch(request_line.decode("utf-8", "replace"), headers)
        except (OSError, ValueError):
            pass
        finally:
            self.shutdown_stream()

    def dispatch(self, request_line: str, headers: dict) -> None:
        parts = request_line.split()
        method = parts[0].upper() if parts else ""
        url = parts[1] if len(parts) > 1 else "*"
        cseq = headers.get("cseq", "0")

        if method == "OPTIONS":
            self.respond(cseq, headers={
                "Public": "OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN, GET_PARAMETER"})

        elif method == "DESCRIBE":
            path = url.rsplit("/", 1)[-1] or "camera"
            if OPTS.paths and path not in OPTS.paths:
                self.respond(cseq, "404 Not Found")
                return
            self.label = path
            sdp = (
                "v=0\r\n"
                "o=- 0 0 IN IP4 127.0.0.1\r\n"
                f"s=SentryView Test Camera {path}\r\n"
                "c=IN IP4 0.0.0.0\r\n"
                "t=0 0\r\n"
                f"m=video 0 RTP/AVP {PAYLOAD_TYPE}\r\n"
                f"a=rtpmap:{PAYLOAD_TYPE} H264/{CLOCK}\r\n"
                f"a=fmtp:{PAYLOAD_TYPE} packetization-mode=1\r\n"
                "a=control:streamid=0\r\n"
            ).encode()
            base = url if url.endswith("/") else url + "/"
            self.respond(cseq, headers={"Content-Type": "application/sdp",
                                        "Content-Base": base}, body=sdp)

        elif method == "SETUP":
            transport = headers.get("transport", "")
            if "tcp" not in transport.lower():
                # UDP would need a second socket pair; TCP interleaved is enough
                # to exercise the client, so steer it there.
                self.respond(cseq, "461 Unsupported Transport")
                return
            channels = "0-1"
            for piece in transport.split(";"):
                if piece.strip().startswith("interleaved="):
                    channels = piece.split("=", 1)[1]
            self.rtp_channel = int(channels.split("-")[0])
            self.respond(cseq, headers={
                "Transport": f"RTP/AVP/TCP;unicast;interleaved={channels}",
                "Session": f"{self.session_id};timeout=60"})

        elif method == "PLAY":
            self.respond(cseq, headers={
                "Session": self.session_id,
                "Range": "npt=0.000-",
                "RTP-Info": f"url={url};seq=1;rtptime=0"})
            if not self.streaming.is_set():
                self.streaming.set()
                self.stream_thread = threading.Thread(target=self.stream, daemon=True)
                self.stream_thread.start()

        elif method in ("PAUSE", "GET_PARAMETER", "SET_PARAMETER"):
            self.respond(cseq, headers={"Session": self.session_id})

        elif method == "TEARDOWN":
            self.respond(cseq, headers={"Session": self.session_id})
            self.closing.set()

        else:
            self.respond(cseq, "501 Not Implemented")

    # --------------------------------------------------------------- streaming
    def stream(self) -> None:
        seq = random.randint(1, 30000)
        ssrc = random.randint(1, 2**31)
        timestamp = 0
        step = CLOCK // max(1, OPTS.fps)
        carry = b""
        pending: list[bytes] = []
        started = time.monotonic()

        try:
            self.proc = subprocess.Popen(encoder_command(self.label),
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, bufsize=0)
        except Exception as exc:                        # pragma: no cover
            print(f"  encoder failed: {exc}", file=sys.stderr)
            return

        print(f"  -> streaming '{self.label}' to {self.client_address[0]}")
        try:
            while not self.closing.is_set():
                if OPTS.duration and time.monotonic() - started > OPTS.duration:
                    break
                chunk = self.proc.stdout.read(32768)
                if not chunk:
                    break
                nals, carry = split_nals(chunk, carry)
                for nal in nals:
                    # A VCL NAL whose first_mb_in_slice is 0 begins a new access
                    # unit; ue(v)==0 means the slice header's first bit is set.
                    is_vcl = (nal[0] & 0x1F) in (1, 5)
                    if (is_vcl and len(nal) > 1 and nal[1] & 0x80
                            and any((n[0] & 0x1F) in (1, 5) for n in pending)):
                        # Parameter sets emitted after the last slice belong to
                        # the *next* picture, so carry them over rather than
                        # tacking them onto the access unit being flushed.
                        cut = max(i for i, n in enumerate(pending)
                                  if (n[0] & 0x1F) in (1, 5)) + 1
                        seq = self._send_au(pending[:cut], seq, timestamp, ssrc)
                        timestamp = (timestamp + step) & 0xFFFFFFFF
                        pending = pending[cut:]
                    pending.append(nal)
        except (OSError, ValueError):
            pass
        finally:
            self.shutdown_stream()
            print(f"  <- stopped '{self.label}' for {self.client_address[0]}")

    def _send_au(self, nals: list, seq: int, timestamp: int, ssrc: int) -> int:
        """Send one access unit; the marker bit lands on its final packet."""
        packets = []
        for nal in nals:
            packets.extend(payload for payload, _ in self._fragment(nal))
        for index, payload in enumerate(packets):
            seq = (seq + 1) & 0xFFFF
            marker = 1 if index == len(packets) - 1 else 0
            self._send_rtp(payload, seq, timestamp, marker, ssrc)
        return seq

    @staticmethod
    def _fragment(nal: bytes):
        """Yield (payload, is_last) as single-NAL or FU-A fragments."""
        if len(nal) <= MTU:
            yield nal, True
            return
        header = nal[0]
        fu_indicator = bytes([(header & 0xE0) | 28])
        nal_type = header & 0x1F
        body = nal[1:]
        size = MTU - 2
        total = (len(body) + size - 1) // size
        for index in range(total):
            start = 0x80 if index == 0 else 0
            end = 0x40 if index == total - 1 else 0
            fu_header = bytes([start | end | nal_type])
            piece = body[index * size:(index + 1) * size]
            yield fu_indicator + fu_header + piece, index == total - 1

    def _send_rtp(self, payload: bytes, seq: int, timestamp: int,
                  marker: int, ssrc: int) -> None:
        header = bytes([
            0x80,
            (marker << 7) | PAYLOAD_TYPE,
            (seq >> 8) & 0xFF, seq & 0xFF,
            (timestamp >> 24) & 0xFF, (timestamp >> 16) & 0xFF,
            (timestamp >> 8) & 0xFF, timestamp & 0xFF,
            (ssrc >> 24) & 0xFF, (ssrc >> 16) & 0xFF,
            (ssrc >> 8) & 0xFF, ssrc & 0xFF,
        ])
        packet = header + payload
        framing = bytes([0x24, self.rtp_channel,
                         (len(packet) >> 8) & 0xFF, len(packet) & 0xFF])
        self.send_raw(framing + packet)

    def shutdown_stream(self) -> None:
        self.closing.set()
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()


class ThreadedRtspServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Fake RTSP camera for testing")
    parser.add_argument("--port", type=int, default=8554)
    parser.add_argument("--count", type=int, default=1,
                        help="serve cam1..camN on this port")
    parser.add_argument("--size", default="1280x720")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--pattern", default="testsrc2",
                        choices=["testsrc2", "smptebars", "testsrc"])
    parser.add_argument("--duration", type=int, default=0,
                        help="stop each stream after N seconds (0 = forever)")
    parser.add_argument("--no-label", action="store_true")
    parser.parse_args(namespace=OPTS)

    OPTS.paths = [f"cam{i}" for i in range(1, max(1, OPTS.count) + 1)]

    server = ThreadedRtspServer(("0.0.0.0", OPTS.port), RtspHandler)
    print(f"SentryView test camera on port {OPTS.port} "
          f"({OPTS.size} @ {OPTS.fps}fps)\n")
    for path in OPTS.paths:
        print(f"  rtsp://127.0.0.1:{OPTS.port}/{path}")
    print("\nCtrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
