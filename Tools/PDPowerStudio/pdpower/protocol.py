"""WeAct Studio PDPowerMini V1.x — protocolo de comunicação (USB CDC e UART).

Referência: planilhas oficiais "USB Communication protocol_v1.2" e
"UART Communication protocol_v1.1" do repositório WeActStudio.PDPowerMiniV1-Buck.

Formato de quadro
-----------------
* USB (CDC, 115200): [cmd, dados..., 0x0A]
* UART (pinos DP/DM, baud configurável): [cmd, dados..., crc8]
  CRC-8 polinômio 0x31, valor inicial 0xFF, sem reflexão.

Leitura = cmd | 0x80. Escritas não têm resposta (sem ACK).
Todos os valores de 16 bits são little-endian.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import IntEnum
from .i18n import tr


TERMINATOR = 0x0A
READ_FLAG = 0x80

USB_VID = 0x1A86
USB_PID = 0xFE0C

UART_BAUDRATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800]


class Cmd(IntEnum):
    WHO_AM_I = 0x01
    OUTPUT_EN = 0x02
    OUTPUT_ID = 0x03
    OUTPUT_DATA = 0x04
    OUTPUT_DISPLAY = 0x05
    OUTPUT_OCP_EN = 0x06
    OUTPUT_OFFSET_EN = 0x07
    BRIGHTNESS = 0x08
    OUTPUT_DISCHARGE_EN = 0x09
    INPUT_STATE = 0x0A          # leitura: estado da entrada; escrita: tensão PD pedida
    SYSTEM_RESET = 0x40
    SYSTEM_UPGRADE = 0x41       # não documentado (usar ferramenta oficial de upgrade)
    SYSTEM_VERSION = 0x42
    SYSTEM_SERIAL_NUM = 0x43
    SYSTEM_CONFIG_SAVE = 0x44
    SYSTEM_FACTORY_RESET = 0x45
    SYSTEM_LCD_PANEL_TYPE = 0x46
    SYSTEM_FACTORY_DATA = 0x47


# Comprimento total da resposta (cabeçalho + dados + terminador/crc). None = string.
RESPONSE_LEN: dict[Cmd, int | None] = {
    Cmd.WHO_AM_I: None,
    Cmd.SYSTEM_VERSION: None,
    Cmd.SYSTEM_SERIAL_NUM: None,
    Cmd.OUTPUT_EN: 3,
    Cmd.OUTPUT_ID: 3,
    Cmd.OUTPUT_DATA: 7,
    Cmd.OUTPUT_DISPLAY: 6,
    Cmd.OUTPUT_OCP_EN: 3,
    Cmd.OUTPUT_OFFSET_EN: 3,
    Cmd.BRIGHTNESS: 3,
    Cmd.INPUT_STATE: 7,
    Cmd.SYSTEM_LCD_PANEL_TYPE: 3,
    Cmd.SYSTEM_FACTORY_DATA: 66,
}


class InputState(IntEnum):
    WAIT = 0
    WAIT_PD_OK = 1
    WAIT_QC_OK = 2
    ERR = 3
    QC = 4
    PD = 5
    DC = 6


INPUT_STATE_LABEL = {
    0: "Aguardando", 1: "Negociando PD", 2: "Negociando QC", 3: "Erro",
    4: "QC", 5: "PD", 6: "DC",
}

LCD_PANEL = {0: "HAN", 1: "BOE"}

# Modo de regulação (bits 2..1 de READ_OUTPUT_STATE)
MODE_CV, MODE_CC, MODE_OC = "CV", "CC", "OC"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    v_min: float
    v_max: float
    i_min: float
    i_max: float
    pd_min: float
    pd_max: float
    input_state_fw: str  # firmware mínimo para INPUT_STATE


MODELS = {
    "AC": ModelSpec("PDPowerMini V1", 0.5, 20.0, 0.005, 3.0, 8.0, 24.0, "1.0.2.0"),
    "B3": ModelSpec("PDPowerMini V1.1", 0.5, 24.0, 0.005, 3.0, 8.0, 28.0, "1.0.1.0"),
}
DEFAULT_MODEL = MODELS["AC"]


def model_from_serial(sn: str | None) -> ModelSpec:
    if sn:
        return MODELS.get(sn[:2].upper(), DEFAULT_MODEL)
    return DEFAULT_MODEL


def version_tuple(ver: str | None) -> tuple[int, ...]:
    if not ver:
        return (0,)
    clean = ver.strip().lstrip("Vv").split("_")[0]
    out = []
    for part in clean.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def version_ge(ver: str | None, target: str) -> bool:
    a, b = version_tuple(ver), version_tuple(target)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) >= b + (0,) * (n - len(b))


def crc8(data: bytes | bytearray) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) if crc & 0x80 else (crc << 1)
            crc &= 0xFF
    return crc


def u16(lo: int, hi: int) -> int:
    return lo | (hi << 8)


def le16(v: int) -> list[int]:
    v = int(v) & 0xFFFF
    return [v & 0xFF, v >> 8]


def build_frame(cmd: int, payload: list[int] | bytes = (), *, read: bool = False, crc: bool = False) -> bytes:
    body = bytearray([(cmd | READ_FLAG) if read else cmd])
    body.extend(payload)
    body.append(crc8(body) if crc else TERMINATOR)
    return bytes(body)


class FrameError(Exception):
    pass


def try_parse(buf: bytearray, cmd: Cmd, *, crc: bool) -> tuple[bytes, int] | None:
    """Procura no buffer a resposta para `cmd`.

    Retorna (payload, bytes_consumidos) ou None se ainda incompleto.
    Bytes lixo antes do cabeçalho são descartados (consumidos).
    Lança FrameError se o quadro estiver corrompido.
    """
    header = cmd | READ_FLAG
    try:
        start = buf.index(header)
    except ValueError:
        return None
    length = RESPONSE_LEN.get(cmd)
    if length is not None:
        if len(buf) - start < length:
            return None
        frame = bytes(buf[start:start + length])
        tail = frame[-1]
        if crc:
            if tail != crc8(frame[:-1]):
                raise FrameError(tr("CRC inválido em {f}", f=frame.hex(' ')))
        elif tail != TERMINATOR:
            raise FrameError(tr("terminador ausente em {f}", f=frame.hex(' ')))
        return frame[1:-1], start + length

    # resposta em string
    if crc:
        if len(buf) - start < 2:
            return None
        n = buf[start + 1]
        total = 2 + n + 1
        if len(buf) - start < total:
            return None
        frame = bytes(buf[start:start + total])
        tail = frame[-1]
        if tail not in (crc8(frame[:-1]), TERMINATOR):
            raise FrameError(tr("CRC inválido em {f}", f=frame.hex(' ')))
        return frame[2:-1], start + total
    try:
        end = buf.index(TERMINATOR, start + 1)
    except ValueError:
        return None
    return bytes(buf[start + 1:end]), end + 1


# ---------------------------------------------------------------- dados de fábrica
@dataclass
class FactoryData:
    """Tabela de calibração (64 bytes). d_* = valor exibido/medido, s_* = valor de ajuste."""
    d_v: list[int] = field(default_factory=lambda: [0] * 7)
    s_v: list[int] = field(default_factory=lambda: [0] * 7)
    d_a: list[int] = field(default_factory=lambda: [0] * 7)
    s_a: list[int] = field(default_factory=lambda: [0] * 7)
    s_0a_s1: int = 0
    s_0a_s0: int = 0
    lcd_type: int = 0

    @classmethod
    def from_bytes(cls, b: bytes) -> "FactoryData":
        if len(b) < 64:
            raise FrameError(tr("dados de fábrica curtos ({n} bytes)", n=len(b)))
        w = [u16(b[k], b[k + 1]) for k in range(0, 64, 2)]
        fd = cls()
        for n in range(7):
            fd.d_v[n], fd.s_v[n] = w[2 * n], w[2 * n + 1]
            fd.d_a[n], fd.s_a[n] = w[14 + 2 * n], w[14 + 2 * n + 1]
        fd.s_0a_s1, fd.s_0a_s0, fd.lcd_type = w[28], w[29], w[30]
        return fd

    def to_bytes(self) -> bytes:
        w: list[int] = []
        for n in range(7):
            w += [self.d_v[n], self.s_v[n]]
        for n in range(7):
            w += [self.d_a[n], self.s_a[n]]
        w += [self.s_0a_s1, self.s_0a_s0, self.lcd_type, 0]
        out = bytearray()
        for x in w:
            out += bytes(le16(x))
        return bytes(out)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FactoryData":
        fd = cls()
        for k in ("d_v", "s_v", "d_a", "s_a"):
            vals = [int(x) for x in d[k]]
            if len(vals) != 7:
                raise ValueError(tr("{k} precisa de 7 valores", k=k))
            setattr(fd, k, vals)
        fd.s_0a_s1 = int(d.get("s_0a_s1", 0))
        fd.s_0a_s0 = int(d.get("s_0a_s0", 0))
        fd.lcd_type = int(d.get("lcd_type", 0))
        return fd
