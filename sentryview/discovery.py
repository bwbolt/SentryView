"""Find cameras on the local network.

Two passes. ONVIF WS-Discovery asks politely over multicast and gets exact
answers from cameras that support it; a TCP sweep of the usual RTSP ports
catches everything else. Anything that answers is then asked, in RTSP, who it
is — the authentication realm and Server header identify most brands, which
is what lets us suggest the right stream path.
"""
from __future__ import annotations

import errno
import ipaddress
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

RTSP_PORTS = [554, 8554, 88, 7447]
THOROUGH_PORTS = [554, 8554, 10554, 1554, 88, 81, 7447, 8080, 8000]
CONNECT_TIMEOUT = 1.0
RTSP_TIMEOUT = 4.0

# Sweeping a whole subnet flat-out overwhelms the OS neighbour (ARP) table:
# live hosts start returning EHOSTDOWN or timing out even though they are
# sitting right there. Moderate concurrency plus a retry of the ambiguous
# results is the difference between finding every camera and finding none.
MAX_WORKERS = 64
RETRY_WORKERS = 16
RETRY_TIMEOUT = 2.0

_SOFT_ERRNOS = {
    errno.ETIMEDOUT, errno.EHOSTDOWN, errno.EHOSTUNREACH,
    errno.ENETUNREACH, errno.EAGAIN, errno.ENOBUFS,
}

_MCAST_ADDR, _MCAST_PORT = "239.255.255.250", 3702

_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <e:Header>
  <w:MessageID>uuid:{mid}</w:MessageID>
  <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
  <w:Action e:mustUnderstand="true">\
http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
 </e:Header>
 <e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>
</e:Envelope>"""

# Identify a camera from its RTSP realm or Server header, and suggest the path
# that brand actually serves. Order matters: first match wins.
_SIGNATURES = [
    ("wyze",              "Wyze",            "/live"),
    ("hikvision",         "Hikvision",       "/Streaming/Channels/101"),
    ("dahua",             "Dahua",           "/cam/realmonitor?channel=1&subtype=0"),
    ("amcrest",           "Amcrest",         "/cam/realmonitor?channel=1&subtype=0"),
    ("reolink",           "Reolink",         "/h264Preview_01_main"),
    ("axis",              "Axis",            "/axis-media/media.amp"),
    ("tp-link",           "TP-Link / Tapo",  "/stream1"),
    ("tapo",              "TP-Link / Tapo",  "/stream1"),
    ("foscam",            "Foscam",          "/videoMain"),
    ("ubnt",              "Ubiquiti UniFi",  "/<stream key>"),
    ("unifi",             "Ubiquiti UniFi",  "/<stream key>"),
    ("hanwha",            "Hanwha",          "/profile2/media.smp"),
    ("vivotek",           "Vivotek",         "/live.sdp"),
    ("bosch",             "Bosch",           "/rtsp_tunnel"),
    ("annke",             "Annke",           "/Streaming/Channels/101"),
    ("lorex",             "Lorex",           "/cam/realmonitor?channel=1&subtype=0"),
    ("swann",             "Swann",           "/ch01/0"),
    ("eufy",              "Eufy",            "/live0"),
]
_DEFAULT_PATHS = ["/live", "/stream1", "/h264", "/"]


@dataclass
class Candidate:
    """One device that looks like a camera."""
    host: str
    port: int = 554
    brand: str = ""
    realm: str = ""
    server: str = ""
    needs_auth: bool = True
    suggested_path: str = ""
    via: str = "scan"           # "onvif" or "scan"
    onvif_url: str = ""

    def suggested_url(self) -> str:
        path = self.suggested_path or "/"
        port = "" if self.port == 554 else f":{self.port}"
        return f"rtsp://{self.host}{port}{path}"

    def describe(self) -> str:
        bits = [self.brand or "RTSP camera"]
        if self.realm and self.realm.lower() not in (self.brand or "").lower():
            bits.append(f"realm “{self.realm}”")
        if self.server:
            bits.append(self.server)
        bits.append("login required" if self.needs_auth else "no login needed")
        return " · ".join(bits)


# ------------------------------------------------------------------ interfaces
def local_ipv4_addresses() -> list:
    """Every usable local IPv4 address, best guess first."""
    found: list = []

    def add(addr: str) -> None:
        if (addr and not addr.startswith("127.") and not addr.startswith("169.254.")
                and addr not in found):
            found.append(addr)

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:                                    # no traffic is sent; just routing
        probe.connect(("8.8.8.8", 80))
        add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return found


def default_ranges() -> list:
    """The /24 around each local address — where a camera almost always is."""
    ranges = []
    for addr in local_ipv4_addresses():
        try:
            network = ipaddress.ip_network(f"{addr}/24", strict=False)
        except ValueError:
            continue
        text = str(network)
        if text not in ranges:
            ranges.append(text)
    return ranges


def hosts_in(range_text: str, limit: int = 4096) -> list:
    """Expand "192.168.1.0/24" or "192.168.1.10-60" into addresses."""
    text = (range_text or "").strip()
    if not text:
        return []
    try:
        if "-" in text:
            head, tail = text.split("-", 1)
            start = ipaddress.ip_address(head.strip())
            tail = tail.strip()
            end = (ipaddress.ip_address(tail) if "." in tail
                   else ipaddress.ip_address(".".join(
                       str(start).split(".")[:3] + [tail])))
            out = []
            current = int(start)
            while current <= int(end) and len(out) < limit:
                out.append(str(ipaddress.ip_address(current)))
                current += 1
            return out
        network = ipaddress.ip_network(text, strict=False)
        return [str(h) for h in list(network.hosts())[:limit]]
    except ValueError:
        try:
            return [str(ipaddress.ip_address(text))]
        except ValueError:
            return []


# ----------------------------------------------------------------------- RTSP
def _recv_response(sock: socket.socket) -> str:
    buf = b""
    while len(buf) < 65536:
        try:
            chunk = sock.recv(8192)
        except (socket.timeout, OSError):
            break
        if not chunk:
            break
        buf += chunk
        if b"\r\n\r\n" in buf:
            break
    return buf.decode("utf-8", "replace")


def rtsp_identify(host: str, port: int = 554,
                  timeout: float = RTSP_TIMEOUT) -> Candidate | None:
    """Speak RTSP to a host and work out what it is."""
    url = f"rtsp://{host}:{port}/"
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.sendall(
            f"OPTIONS {url} RTSP/1.0\r\nCSeq: 1\r\n"
            f"User-Agent: SentryView\r\n\r\n".encode())
        options = _recv_response(sock)
    except OSError:
        return None
    finally:
        sock.close()

    if "RTSP/1.0" not in options:
        return None

    candidate = Candidate(host=host, port=port)
    server = re.search(r"(?im)^Server:\s*(.+)$", options)
    if server:
        candidate.server = server.group(1).strip()[:60]

    # DESCRIBE reveals the authentication realm, which names the brand.
    sock = socket.socket()
    sock.settimeout(timeout)
    describe = ""
    try:
        sock.connect((host, port))
        sock.sendall(
            f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n"
            f"User-Agent: SentryView\r\n\r\n".encode())
        describe = _recv_response(sock)
    except OSError:
        pass
    finally:
        sock.close()

    realm = re.search(r'realm="([^"]+)"', describe)
    if realm:
        candidate.realm = realm.group(1)[:48]
    if not candidate.server:
        server = re.search(r"(?im)^Server:\s*(.+)$", describe)
        if server:
            candidate.server = server.group(1).strip()[:60]
    candidate.needs_auth = "401" in describe.split("\r\n", 1)[0] or bool(realm)

    haystack = f"{candidate.realm} {candidate.server}".lower()
    for needle, brand, path in _SIGNATURES:
        if needle in haystack:
            candidate.brand, candidate.suggested_path = brand, path
            break
    else:
        candidate.suggested_path = "/live" if port == 554 else "/"
    return candidate


# -------------------------------------------------------------------- ONVIF
def onvif_probe(timeout: float = 4.0) -> list:
    """Multicast WS-Discovery; returns (host, service url) pairs."""
    results: dict = {}
    sockets = []
    sources = local_ipv4_addresses() + ["0.0.0.0"]
    for addr in sources:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
            if addr != "0.0.0.0":
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                socket.inet_aton(addr))
            sock.bind((addr, 0))
            sock.settimeout(0.3)
            sock.sendto(_PROBE.format(mid=uuid.uuid4()).encode(),
                        (_MCAST_ADDR, _MCAST_PORT))
            sockets.append(sock)
        except OSError:
            continue

    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for sock in sockets:
            try:
                data, src = sock.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            text = data.decode("utf-8", "replace")
            urls = re.findall(r"https?://[^\s<]+", text)
            results.setdefault(src[0], urls[0] if urls else "")
    for sock in sockets:
        try:
            sock.close()
        except OSError:
            pass
    return list(results.items())


# -------------------------------------------------------------------- sweep
# "? (192.168.1.1) at 3c:37:86:19:6c:db on en1" / "192.168.1.1  3c-37-86-..."
# The separator cannot be "non-hex characters": macOS writes " at ", and "a"
# is a hex digit.
_ARP_RE = re.compile(
    r"(\d{1,3}(?:\.\d{1,3}){3})\D*?"
    r"((?:[0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2})")


def _arp_table() -> dict:
    """Read the OS neighbour table: {ip: mac}."""
    command = ["arp", "-a"] if sys.platform == "win32" else ["arp", "-an"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        out = subprocess.run(command, capture_output=True, timeout=15,
                             creationflags=flags).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in out.splitlines():
        if "incomplete" in line.lower():
            continue
        m = _ARP_RE.search(line)
        if m:
            table[m.group(1)] = m.group(2)
    return table


def live_hosts(hosts: list, settle: float = 1.6) -> list:
    """Which of these addresses actually exist, via ARP.

    A UDP datagram to a closed port forces the OS to resolve the hardware
    address whether or not anything replies, so afterwards the neighbour
    table lists exactly the hosts that are present. That turns a subnet sweep
    from "time out on 240 dead addresses" into one quick pass.
    """
    before = _arp_table()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    for host in hosts:
        try:
            sock.sendto(b"\x00", (host, 9))       # discard port
        except OSError:
            continue
    sock.close()
    time.sleep(settle)
    table = _arp_table()
    table.update({k: v for k, v in before.items() if k not in table})
    wanted = set(hosts)
    return [h for h in hosts if h in wanted and h in table]


OPEN, CLOSED, UNSURE = "open", "closed", "unsure"


def _probe_port(host: str, port: int, timeout: float) -> str:
    """OPEN, CLOSED (actively refused) or UNSURE (no answer — may be a miss)."""
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return OPEN
    except socket.timeout:
        return UNSURE
    except OSError as exc:
        if getattr(exc, "errno", None) in _SOFT_ERRNOS:
            return UNSURE
        return CLOSED
    finally:
        sock.close()


def _port_open(args) -> tuple:
    host, port = args
    return host, port, _probe_port(host, port, CONNECT_TIMEOUT)


def _retry(args) -> tuple:
    host, port = args
    return host, port, _probe_port(host, port, RETRY_TIMEOUT)


def scan(ranges: list, ports: list | None = None, on_progress=None,
         on_found=None, cancel: threading.Event | None = None) -> list:
    """Sweep ranges for RTSP, identifying whatever answers.

    ``on_progress(done, total, note)`` and ``on_found(candidate)`` are called
    as work completes. Set ``cancel`` to stop early.
    """
    ports = ports or RTSP_PORTS
    cancel = cancel or threading.Event()
    found: list = []
    seen: set = set()

    def report(candidate: Candidate) -> None:
        if candidate.host in seen:
            return
        seen.add(candidate.host)
        found.append(candidate)
        if on_found:
            on_found(candidate)

    # Pass 1 - ONVIF cameras announce themselves, no scanning needed.
    if on_progress:
        on_progress(0, 1, "Asking cameras to announce themselves…")
    if not cancel.is_set():
        for host, service in onvif_probe():
            if cancel.is_set():
                break
            candidate = rtsp_identify(host) or Candidate(host=host)
            candidate.via = "onvif"
            candidate.onvif_url = service
            if not candidate.brand and service:
                candidate.brand = "ONVIF camera"
            report(candidate)

    # Pass 2 - sweep the usual RTSP ports.
    all_hosts = [host for r in ranges for host in hosts_in(r)]

    # Narrow to hosts that exist before touching any port. Falls back to the
    # full sweep when the neighbour table is unreadable or the range is not
    # on this link (ARP only covers the local segment).
    if on_progress:
        on_progress(0, 1, "Looking for devices on the network…")
    present = live_hosts(all_hosts) if not cancel.is_set() else []
    if present:
        scan_hosts = present
        if on_progress:
            on_progress(0, 1, f"{len(present)} device(s) present — checking them…")
    else:
        scan_hosts = all_hosts

    targets = [(host, port) for host in scan_hosts for port in ports]
    total = len(targets) or 1
    done = 0
    hits: list = []
    unsure: list = []

    if targets and not cancel.is_set():
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for host, port, state in pool.map(_port_open, targets):
                done += 1
                if cancel.is_set():
                    break
                if state == OPEN:
                    hits.append((host, port))
                elif state == UNSURE:
                    unsure.append((host, port))
                if on_progress and done % 25 == 0:
                    on_progress(done, total,
                                f"Scanning… {len(hits)} responded so far")

    # Second look at everything that simply did not answer. After the first
    # pass the ARP table is warm, so a slower retry resolves the false misses.
    if unsure and not cancel.is_set():
        if on_progress:
            on_progress(done, total, f"Double-checking {len(unsure)} address(es)…")
        with ThreadPoolExecutor(max_workers=RETRY_WORKERS) as pool:
            for host, port, state in pool.map(_retry, unsure):
                if cancel.is_set():
                    break
                if state == OPEN and (host, port) not in hits:
                    hits.append((host, port))

    if on_progress:
        on_progress(total, total, f"Identifying {len(hits)} device(s)…")

    # Pass 3 - ask each responder what it is.
    for host, port in hits:
        if cancel.is_set():
            break
        if host in seen:
            continue
        candidate = rtsp_identify(host, port)
        if candidate:
            report(candidate)

    found.sort(key=lambda c: tuple(int(p) for p in c.host.split("."))
               if c.host.count(".") == 3 and c.host.replace(".", "").isdigit()
               else (0, 0, 0, 0))
    return found
