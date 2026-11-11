"""Traduções das mensagens do backend (erros, eventos, passos da automação).

O texto em português é a chave; `tr()` devolve a versão do idioma ativo já formatada.
Idiomas: pt-BR (padrão), en, zh-CN. A interface web tem o próprio dicionário em web/i18n.js.
"""
from __future__ import annotations

LANGS = ("pt-BR", "en", "zh-CN")
_lang = "pt-BR"

# chave pt-BR: (inglês, chinês simplificado)
_T: dict[str, tuple[str, str]] = {
    # ------------------------------------------------------------ dispositivo
    "link deve ser 'usb' ou 'uart'": ("link must be 'usb' or 'uart'", "link 必须是 'usb' 或 'uart'"),
    "PDPowerMini não encontrado (USB 1A86:FE0C). Conecte a placa ou informe a porta.":
        ("PDPowerMini not found (USB 1A86:FE0C). Plug in the board or choose the port.",
         "未找到 PDPowerMini（USB 1A86:FE0C）。请连接设备或指定端口。"),
    "Não foi possível abrir {port}: {e}": ("Could not open {port}: {e}", "无法打开 {port}：{e}"),
    "Dispositivo não respondeu em {port}: {e}": ("Device did not respond on {port}: {e}", "{port} 上的设备无响应：{e}"),
    "Conectado a {name} em {port} ({link})": ("Connected to {name} on {port} ({link})", "已连接 {name}，端口 {port}（{link}）"),
    "Desconectado": ("Disconnected", "已断开"),
    "Saída LIGADA": ("Output ON", "输出已开启"),
    "Saída DESLIGADA": ("Output OFF", "输出已关闭"),
    "Configuração salva na flash do dispositivo": ("Settings saved to the device flash", "设置已保存到设备闪存"),
    "Dispositivo não conectado": ("Device not connected", "设备未连接"),
    "sem resposta para {cmd}": ("no response to {cmd}", "{cmd} 无响应"),
    "Conexão perdida: {e}": ("Connection lost: {e}", "连接丢失：{e}"),
    "Conexão com o dispositivo perdida: {e}": ("Connection to the device lost: {e}", "与设备的连接已断开：{e}"),
    "Tensão {v:.3f} V fora do permitido ({lo}–{hi} V)":
        ("Voltage {v:.3f} V out of range ({lo}–{hi} V)", "电压 {v:.3f} V 超出允许范围（{lo}–{hi} V）"),
    "Corrente {i:.3f} A fora do permitido ({lo}–{hi} A)":
        ("Current {i:.3f} A out of range ({lo}–{hi} A)", "电流 {i:.3f} A 超出允许范围（{lo}–{hi} A）"),
    "preset deve ser 0–4": ("preset must be 0–4", "预设必须为 0–4"),
    "Desligue a saída antes de trocar de preset": ("Turn the output off before switching presets", "切换预设前请先关闭输出"),
    "brilho deve ser 0–100": ("brightness must be 0–100", "亮度必须为 0–100"),
    "Tensão PD deve ficar entre {lo} e {hi} V": ("PD voltage must be between {lo} and {hi} V", "PD 电压必须在 {lo} 到 {hi} V 之间"),
    "A tensão PD só é aplicada com a saída desligada":
        ("The PD voltage can only be changed with the output off", "仅在输出关闭时才能设置 PD 电压"),
    "Reset do sistema enviado": ("System reset sent", "已发送系统复位"),
    "Restauração de fábrica enviada": ("Factory reset sent", "已发送恢复出厂设置"),
    "tipo de LCD deve ser 0 (HAN) ou 1 (BOE)": ("LCD type must be 0 (HAN) or 1 (BOE)", "LCD 类型必须为 0（HAN）或 1（BOE）"),
    "Tipo de painel LCD gravado: {p}": ("LCD panel type written: {p}", "已写入 LCD 面板类型：{p}"),
    "Verificação falhou: dados lidos diferem dos gravados":
        ("Verification failed: read-back data differs from what was written", "校验失败：读回数据与写入数据不一致"),
    "Dados de calibração gravados e verificados": ("Calibration data written and verified", "校准数据已写入并校验"),
    # estados da entrada
    "Aguardando": ("Waiting", "等待中"),
    "Negociando PD": ("Negotiating PD", "正在协商 PD"),
    "Negociando QC": ("Negotiating QC", "正在协商 QC"),
    "Erro": ("Error", "错误"),
    # ------------------------------------------------------------ protocolo
    "CRC inválido em {f}": ("invalid CRC in {f}", "CRC 校验错误：{f}"),
    "terminador ausente em {f}": ("missing terminator in {f}", "缺少结束符：{f}"),
    "dados de fábrica curtos ({n} bytes)": ("factory data too short ({n} bytes)", "出厂数据过短（{n} 字节）"),
    "{k} precisa de 7 valores": ("{k} needs 7 values", "{k} 需要 7 个值"),
    # ------------------------------------------------------------ servidor / serviço
    "confirmação necessária": ("confirmation required", "需要确认"),
    "nome vazio": ("empty name", "名称为空"),
    "marcador": ("marker", "标记"),
    "chave de config não permitida: {k}": ("config key not allowed: {k}", "不允许的配置键：{k}"),
    "Potência acima de 45 W": ("Power above 45 W", "功率超过 45 W"),
    "Autoconexão falhou: {e}":("Auto-connect failed: {e}", "自动连接失败：{e}"),
    "idioma não suportado: {lang}": ("unsupported language: {lang}", "不支持的语言：{lang}"),
    # ------------------------------------------------------------ automação
    "expressão não permitida: {name}": ("expression not allowed: {name}", "不允许的表达式：{name}"),
    "só abs/min/max/round/sqrt são permitidas": ("only abs/min/max/round/sqrt are allowed", "仅允许 abs/min/max/round/sqrt"),
    "só constantes numéricas": ("only numeric constants are allowed", "仅允许数值常量"),
    "guarda inválida '{cond}': {e}": ("invalid guard '{cond}': {e}", "无效的保护条件 '{cond}'：{e}"),
    "falha ao desligar saída: {e}": ("failed to turn the output off: {e}", "关闭输出失败：{e}"),
    "⚠ GUARDA ACIONADA: {msg}  (v={v:.3f} V, i={i:.3f} A, p={p:.2f} W) → saída desligada":
        ("⚠ GUARD TRIPPED: {msg}  (v={v:.3f} V, i={i:.3f} A, p={p:.2f} W) → output off",
         "⚠ 保护触发：{msg}  (v={v:.3f} V, i={i:.3f} A, p={p:.2f} W) → 输出已关闭"),
    "Guarda: {msg}": ("Guard: {msg}", "保护：{msg}"),
    "sequência sem passos": ("sequence has no steps", "序列没有步骤"),
    "op desconhecida: {op}": ("unknown op: {op}", "未知操作：{op}"),
    "Já existe uma automação em execução": ("An automation is already running", "已有自动化正在运行"),
    "Sequência": ("Sequence", "序列"),
    "Parada solicitada": ("Stop requested", "已请求停止"),
    "▶ Iniciando '{name}'": ("▶ Starting '{name}'", "▶ 开始执行 '{name}'"),
    "abortada": ("aborted", "已中止"),
    "erro: {e}": ("error: {e}", "错误：{e}"),
    "Erro: {e}": ("Error: {e}", "错误：{e}"),
    "■ '{name}' finalizada ({result})": ("■ '{name}' finished ({result})", "■ '{name}' 已结束（{result}）"),
    "Ajustar {v} V / {i} A": ("Set {v} V / {i} A", "设置 {v} V / {i} A"),
    "Saída {s}": ("Output {s}", "输出 {s}"),
    "Aguardar {s} s": ("Wait {s} s", "等待 {s} s"),
    "Marcador": ("Marker", "标记"),
    "Zerar contadores": ("Reset counters", "计数器清零"),
    "Salvar config": ("Save config", "保存配置"),
    "Medir {s} s": ("Measure {s} s", "测量 {s} s"),
    "Medida {label}: {v:.3f} V  {i:.4f} A  {p:.3f} W":
        ("Measurement {label}: {v:.3f} V  {i:.4f} A  {p:.3f} W", "测量 {label}：{v:.3f} V  {i:.4f} A  {p:.3f} W"),
    "Aguardar até {cond}": ("Wait until {cond}", "等待直到 {cond}"),
    "timeout em wait_until({cond}) — continuando": ("timeout in wait_until({cond}) — continuing", "wait_until({cond}) 超时 — 继续执行"),
    "timeout aguardando {cond}": ("timeout waiting for {cond}", "等待 {cond} 超时"),
    "Condição atingida: {cond}": ("Condition met: {cond}", "条件已满足：{cond}"),
    "Ciclo {k}/{n}": ("Cycle {k}/{n}", "循环 {k}/{n}"),
    "Rampa {p} {a}→{b} ({n} pts)": ("Ramp {p} {a}→{b} ({n} pts)", "斜坡 {p} {a}→{b}（{n} 点）"),
    "Rampa {p} = {x:g} ({k}/{n})": ("Ramp {p} = {x:g} ({k}/{n})", "斜坡 {p} = {x:g}（{k}/{n}）"),
    "configuração desconhecida: {name}": ("unknown setting: {name}", "未知设置：{name}"),
}


def normalize(lang: str | None) -> str:
    """Aceita 'en', 'en-US', 'zh', 'zh-CN', 'pt', 'pt-BR'…"""
    s = (lang or "").strip().lower()
    if s.startswith("zh"):
        return "zh-CN"
    if s.startswith("en"):
        return "en"
    if s.startswith("pt"):
        return "pt-BR"
    return ""


def set_lang(lang: str | None) -> str:
    global _lang
    _lang = normalize(lang) or "pt-BR"
    return _lang


def system_lang() -> str:
    """Idioma da interface do Windows (ou do locale), normalizado; inglês se não for pt/zh."""
    try:
        import ctypes
        import locale
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        name = locale.windows_locale.get(lid, "")
    except Exception:
        import locale
        name = (locale.getlocale()[0] or "")
    return normalize(name.replace("_", "-")) or "en"


def get_lang() -> str:
    return _lang


def tr(msg: str, **kw) -> str:
    """Traduz `msg` (texto pt-BR) para o idioma ativo e aplica `str.format(**kw)`."""
    if _lang != "pt-BR":
        t = _T.get(msg)
        if t:
            msg = t[0] if _lang == "en" else t[1]
    if kw:
        try:
            return msg.format(**kw)
        except (KeyError, IndexError, ValueError):
            return msg
    return msg
