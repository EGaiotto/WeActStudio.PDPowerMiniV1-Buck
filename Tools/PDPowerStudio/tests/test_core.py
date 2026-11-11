import time

import pytest

from pdpower.protocol import Cmd, FactoryData, build_frame, crc8, try_parse
from pdpower.device import Device, DeviceError
from pdpower.recorder import Recorder
from pdpower.automation import Automation, compile_cond


def test_crc_matches_spreadsheet():
    # valores tabelados na planilha UART v1.1
    table = {0x81: 0xE7, 0x82: 0xB4, 0x83: 0x85, 0x85: 0x23, 0x86: 0x70, 0x87: 0x41, 0x88: 0x6F,
             0x8A: 0x0D, 0xC2: 0x89, 0xC3: 0xB8, 0xC6: 0x4D, 0xC7: 0x7C, 0x40: 0x91, 0x44: 0x55, 0x45: 0x64}
    for b, c in table.items():
        assert crc8(bytes([b])) == c, hex(b)


def test_frames():
    assert build_frame(Cmd.OUTPUT_DATA, [0, 0x88, 0x13, 0xC8, 0], read=False) == bytes([4, 0, 0x88, 0x13, 0xC8, 0, 10])
    assert build_frame(Cmd.WHO_AM_I, read=True) == b"\x81\n"
    # dados com 0x0A no meio não confundem o parser de tamanho fixo
    buf = bytearray(b"\xff\x84\x01\x0a\x0a\x0a\x00\x0a")
    payload, used = try_parse(buf, Cmd.OUTPUT_DATA, crc=False)
    assert payload == b"\x01\x0a\x0a\x0a\x00" and used == 8
    # string USB
    assert try_parse(bytearray(b"\xc2V1.0\n"), Cmd.SYSTEM_VERSION, crc=False)[0] == b"V1.0"


def test_factory_roundtrip():
    raw = bytes.fromhex("2f00000014 04ed03d60bbb0b87138a13e22612 27493a9a3ac8493a4a07000000380031006301"
                        "5c01ef02e802db05d005c009b009b30ba10b0100000000000000".replace(" ", ""))
    fd = FactoryData.from_bytes(raw)
    assert fd.d_v[1] == 1044 and fd.s_v[1] == 1005 and fd.d_a[6] == 2995 and fd.s_0a_s1 == 1
    assert fd.to_bytes() == raw


@pytest.mark.parametrize("link", ["usb", "uart"])
def test_device_sim(link):
    d = Device()
    d.connect(simulate=True, link=link)
    try:
        s = d.snapshot()
        assert s["connected"] and s["info"]["serial"].startswith("AC")
        d.set_vi(5.0, 0.3)
        assert d.snapshot()["presets"][0] == {"v": 5.0, "i": 0.3}
        d.set_output(True)
        time.sleep(0.4)
        s = d.snapshot()
        assert s["output"]["on"] and s["output"]["mode"] == "CC"   # 10 Ω → 0.5 A > 0.3 A
        assert 2.8 < s["meas"]["v"] < 3.2
        with pytest.raises(DeviceError):
            d.set_vi(25.0)
        with pytest.raises(DeviceError):
            d.select_preset(2)          # saída ligada
        d.set_output(False)
        d.set_brightness(55)
        assert d.snapshot()["settings"]["brightness"] == 55
        d.set_pd_voltage(15.0)
        assert d.snapshot()["input"]["pd_req"] == 15.0
        fd = d.read_factory()
        d.write_factory(fd)
    finally:
        d.disconnect()


def test_cond():
    f = compile_cond("i < 0.05 and v > 4")
    assert f({"i": 0.01, "v": 4.2}) and not f({"i": 0.2, "v": 4.2})
    for bad in ("__import__('os')", "v.__class__", "open('x')", "'a'"):
        with pytest.raises(Exception):
            compile_cond(bad)


def test_sequence_sweep():
    d, r = Device(), Recorder()
    d.sample_listeners.append(r.add)
    d.connect(simulate=True)
    a = Automation(d, r)
    try:
        a.start({"name": "t", "steps": [
            {"op": "set", "v": 1.0, "i": 2.0}, {"op": "output", "on": True},
            {"op": "ramp", "param": "v", "from": 1, "to": 3, "step": 1, "dwell": 0.3},
            {"op": "wait_until", "cond": "v > 2.5", "timeout": 2},
        ]})
        assert a.wait(10)
        assert a.status["result"] == "ok", a.status
        sw = a.results["sweep"]
        assert [p["set"] for p in sw] == [1.0, 2.0, 3.0]
        assert abs(sw[-1]["i"] - 0.3) < 0.03
        assert not d.snapshot()["output"]["on"]     # on_end=off
    finally:
        d.disconnect()
