"""Minimal telnet protocol handling (replaces sol-telnet).

We only care about two things from the client: lines of input and window size
(NAWS, RFC 1073). Every other negotiation is parsed and ignored.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240

CR = 13
LF = 10
NUL = 0

OPT_NAWS = 31


@dataclass(frozen=True)
class Line:
    text: str


@dataclass(frozen=True)
class WindowSize:
    width: int
    height: int


Event = Line | WindowSize


class TelnetParser:
    """Byte-at-a-time state machine turning raw telnet input into events."""

    def __init__(self) -> None:
        self._state = "data"
        self._line = bytearray()
        self._command = 0
        self._sb_option = 0
        self._sb_bytes = bytearray()

    def feed(self, data: bytes) -> list[Event]:
        events: list[Event] = []
        for byte in data:
            match self._state:
                case "data":
                    if byte == IAC:
                        self._state = "iac"
                    elif byte == CR:
                        self._state = "cr"
                    elif byte == LF:
                        # Some clients send a bare LF for newlines.
                        events.append(self._take_line())
                    else:
                        self._line.append(byte)
                case "cr":
                    self._state = "data"
                    if byte in (LF, NUL):
                        events.append(self._take_line())
                    elif byte == IAC:
                        self._state = "iac"
                    else:
                        # CR followed by something else breaks RFC 854; treat the CR as end of line.
                        events.append(self._take_line())
                        self._line.append(byte)
                case "iac":
                    if byte == IAC:
                        self._line.append(IAC)
                        self._state = "data"
                    elif byte in (WILL, WONT, DO, DONT):
                        self._command = byte
                        self._state = "option"
                    elif byte == SB:
                        self._state = "sb-option"
                    else:
                        # NOP, AYT, GA, etc. carry no data for us.
                        self._state = "data"
                case "option":
                    self._state = "data"
                case "sb-option":
                    self._sb_option = byte
                    self._sb_bytes.clear()
                    self._state = "sb"
                case "sb":
                    if byte == IAC:
                        self._state = "sb-iac"
                    else:
                        self._sb_bytes.append(byte)
                case "sb-iac":
                    if byte == SE:
                        self._state = "data"
                        if event := self._subnegotiation():
                            events.append(event)
                    else:
                        # IAC IAC is an escaped 255; anything else is a sloppy client.
                        if byte != IAC:
                            self._sb_bytes.append(IAC)
                        self._sb_bytes.append(byte)
                        self._state = "sb"
        return events

    def _take_line(self) -> Line:
        text = self._line.decode("utf-8", errors="replace")
        self._line.clear()
        return Line(text)

    def _subnegotiation(self) -> WindowSize | None:
        if self._sb_option == OPT_NAWS and len(self._sb_bytes) >= 4:
            b = self._sb_bytes
            return WindowSize(width=(b[0] << 8) | b[1], height=(b[2] << 8) | b[3])
        return None


class TelnetConnection(asyncio.Protocol):
    """asyncio protocol that feeds parsed events into a queue for a session coroutine."""

    def __init__(self, on_connect: Callable[[TelnetConnection], None]) -> None:
        self._on_connect = on_connect
        self._parser = TelnetParser()
        self.transport: asyncio.Transport | None = None
        self.events: asyncio.Queue[Event | None] = asyncio.Queue()
        self.peer: str = "?"

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.Transport)
        self.transport = transport
        peer = transport.get_extra_info("peername")
        self.peer = f"{peer[0]}:{peer[1]}" if peer else "?"
        # Ask the client to tell us its window size.
        transport.write(bytes([IAC, DO, OPT_NAWS]))
        self._on_connect(self)

    def data_received(self, data: bytes) -> None:
        for event in self._parser.feed(data):
            self.events.put_nowait(event)

    def connection_lost(self, exc: Exception | None) -> None:
        self.events.put_nowait(None)

    @property
    def closed(self) -> bool:
        return self.transport is None or self.transport.is_closing()

    def send(self, text: str) -> None:
        if self.closed:
            return
        # Normalise bare LFs to CRLF and escape IAC bytes.
        data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
        self.transport.write(data.replace(bytes([IAC]), bytes([IAC, IAC])))

    def send_line(self, line: str = "") -> None:
        self.send(line + "\r\n")

    def close(self) -> None:
        if not self.closed:
            self.transport.close()
