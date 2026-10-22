from spaceclaw.telnet import DO, IAC, OPT_NAWS, SB, SE, WILL, Line, TelnetParser, WindowSize


def test_lines_with_various_newlines():
    p = TelnetParser()
    assert p.feed(b"one\r\ntwo\r\x00three\nfour") == [Line("one"), Line("two"), Line("three")]
    assert p.feed(b"\r\n") == [Line("four")]


def test_naws_and_negotiation_are_stripped():
    p = TelnetParser()
    data = (
        bytes([IAC, WILL, OPT_NAWS])
        + bytes([IAC, SB, OPT_NAWS, 0, 132, 0, 50, IAC, SE])
        + b"hi"
        + bytes([IAC, DO, 1])
        + b"!\r\n"
    )
    assert p.feed(data) == [WindowSize(132, 50), Line("hi!")]


def test_naws_with_escaped_255():
    p = TelnetParser()
    data = bytes([IAC, SB, OPT_NAWS, 0, IAC, IAC, 0, 40, IAC, SE])
    assert p.feed(data) == [WindowSize(255, 40)]


def test_split_across_packets():
    p = TelnetParser()
    data = bytes([IAC, SB, OPT_NAWS, 0, 100, 0, 30, IAC, SE]) + b"abc\r\n"
    events = [e for b in data for e in p.feed(bytes([b]))]
    assert events == [WindowSize(100, 30), Line("abc")]
