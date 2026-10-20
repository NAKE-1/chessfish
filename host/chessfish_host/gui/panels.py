"""Right-hand stats tabs. Each panel has small update methods; MainWindow feeds them."""
import time

import chess
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QGridLayout, QHeaderView, QLabel, QPlainTextEdit, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..stats import metrics, openings

pg.setConfigOptions(antialias=True, background="#1e1f22", foreground="#c8c8c8")
CLASS_COLORS = {"best": "#4caf50", "excellent": "#8bc34a", "good": "#9e9e9e", "inaccuracy": "#e6c229",
                "mistake": "#f08a24", "blunder": "#e53935"}


def fmt_cp(cp: int | None, mate: int | None = None) -> str:
    if mate is not None:
        return f"#{mate}"
    if cp is None:
        return "–"
    if abs(cp) >= 9000:
        n = (metrics.MATE_CP - abs(cp) + 1) // 2
        return f"#{'-' if cp < 0 else ''}{n}"
    return f"{cp / 100:+.2f}"


def fmt_n(n) -> str:
    if n is None:
        return "–"
    for unit, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)


def fmt_ms(ms) -> str:
    if ms is None:
        return "–"
    s = max(0, ms) / 1000
    if s < 10:
        return f"{s:.1f}"
    return f"{int(s // 60)}:{int(s % 60):02d}"


def table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionMode(QTableWidget.NoSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def set_row(t: QTableWidget, row: int, values: list, colors: dict[int, str] | None = None):
    if t.rowCount() <= row:
        t.setRowCount(row + 1)
    for c, v in enumerate(values):
        item = t.item(row, c)
        if item is None:
            item = QTableWidgetItem()
            t.setItem(row, c, item)
        item.setText(str(v))
        if colors and c in colors:
            item.setForeground(pg.mkColor(colors[c]))


class EnginePanel(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self.header = QLabel("–")
        self.header.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.threat = QLabel("Threat: –")
        self.lines = table(["#", "Eval", "W / D / L %", "Depth", "Nodes", "NPS", "Hash", "TB", "Time", "Principal variation"])
        self.lines.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        self.lines.setWordWrap(True)
        lay.addWidget(self.header)
        lay.addWidget(self.threat)
        lay.addWidget(self.lines)

    def update_snap(self, snap: dict):
        lines = snap["lines"]
        self.lines.setRowCount(len(lines))
        for i, l in enumerate(lines):
            wdl = " / ".join(f"{x / 10:.1f}" for x in l["wdl"]) if l["wdl"] else "–"
            set_row(self.lines, i, [l["multipv"], fmt_cp(l["cp"], l["mate"]), wdl, f"{l['depth']}/{l['seldepth']}",
                                    fmt_n(l["nodes"]), fmt_n(l["nps"]), f"{(l['hashfull'] or 0) / 10:.1f}%",
                                    fmt_n(l["tbhits"]), f"{l['time'] or 0:.1f}s", l["pv_san"]])
        if lines:
            b = lines[0]
            self.header.setText(f"Depth {b['depth']}/{b['seldepth']} · {fmt_n(b['nodes'])} nodes · {fmt_n(b['nps'])} nps · "
                                f"hash {(b['hashfull'] or 0) / 10:.1f}% · tbhits {b['tbhits'] or 0} · {b['time'] or 0:.1f}s")
        t = snap.get("threat")
        self.threat.setText(f"Threat (if side to move passed): {t['san']} ({fmt_cp(t['cp'])})" if t else "Threat: –")


class ProbablePanel(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self.title = QLabel("–")
        self.predicted = QLabel("–")
        self.predicted.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.table = table(["Move", "Probability", "Eval (mover)", "Swing", "Predicted reply"])
        lay.addWidget(self.title)
        lay.addWidget(self.predicted)
        lay.addWidget(self.table)

    def update_moves(self, board: chess.Board, lines: list[dict], elo: int, is_friend: bool):
        moves = metrics.probable_moves(board, lines, elo)
        who = "Friend" if is_friend else "Your side"
        self.title.setText(f"{who} to move ({'white' if board.turn else 'black'}) · softmax T = {40 + (3200 - elo) / 10:.0f} cp (Elo {elo})")
        if moves:
            top = max(moves, key=lambda m: m["prob"])
            self.predicted.setText(f"Most likely: {top['san']} ({top['prob']:.0%})" + (f" → then {top['reply']}" if top["reply"] else ""))
        self.table.setRowCount(len(moves))
        for i, m in enumerate(sorted(moves, key=lambda m: -m["prob"])):
            set_row(self.table, i, [m["san"], f"{m['prob']:.1%}", fmt_cp(int(m["cp"])), f"{m['swing'] / 100:+.2f}", m["reply"] or "–"])


class GamePanel(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self.plot = pg.PlotWidget()
        self.plot.setYRange(-5, 5)
        self.plot.setLabel("left", "eval (pawns, white)")
        self.plot.setMaximumHeight(170)
        self.plot.addLine(y=0, pen=pg.mkPen("#555"))
        self.curve = self.plot.plot([], [], pen=pg.mkPen("#e0e0e0", width=2), fillLevel=0, brush=(220, 220, 220, 40))
        lay.addWidget(self.plot)
        grid = QGridLayout()
        self.info = {k: QLabel("–") for k in ("opening", "phase", "material", "mobility", "hanging", "king", "white", "black")}
        for i, (k, label) in enumerate((("opening", "Opening"), ("phase", "Phase"), ("material", "Material"),
                                        ("mobility", "Mobility"), ("hanging", "Hanging"), ("king", "King-zone attackers"),
                                        ("white", "White"), ("black", "Black"))):
            grid.addWidget(QLabel(f"<b>{label}</b>"), i // 2, (i % 2) * 2)
            self.info[k].setWordWrap(True)
            grid.addWidget(self.info[k], i // 2, (i % 2) * 2 + 1)
        lay.addLayout(grid)
        self.moves = table(["#", "Move", "Grade", "Eval", "Acc %", "CP loss", "Think", "Clock"])
        lay.addWidget(self.moves)

    def update_position(self, board: chess.Board):
        op = openings.lookup(board)
        self.info["opening"].setText(f"{op[0]} {op[1]}" if op else "–")
        self.info["phase"].setText(metrics.phase(board))
        m = metrics.material(board)
        self.info["material"].setText(f"W {m['white']} · B {m['black']} ({m['balance']:+d})")
        mob = metrics.mobility(board)
        self.info["mobility"].setText(f"W {mob['white']} · B {mob['black']}")
        self.info["hanging"].setText(", ".join(metrics.hanging(board)) or "none")
        k = metrics.king_zone_attacks(board)
        self.info["king"].setText(f"W king {k.get('white', 0)} · B king {k.get('black', 0)}")

    def update_record(self, rec):
        if rec is None:
            return
        xs, ys = [], []
        start = chess.Board(rec.start_fen)
        offset = 0 if start.turn == chess.WHITE else 1
        self.moves.setRowCount(len(rec.plies))
        for i, p in enumerate(rec.plies):
            s = rec.ply_stats(i)
            num = f"{start.fullmove_number + (i + offset) // 2}{'.' if p.mover == chess.WHITE else '…'}"
            if s:
                xs.append(i + 1)
                ys.append(max(-5, min(5, s["cp_after"] / 100)))
                set_row(self.moves, i, [num, p.san, s["class"] + (" " + metrics.NAGS[s["class"]] if s["class"] in metrics.NAGS else ""),
                                        fmt_cp(s["cp_after"]), f"{s['accuracy']:.0f}", f"{s['cp_loss']:.0f}",
                                        fmt_ms(p.think_ms), fmt_ms(p.clock_ms)], {2: CLASS_COLORS[s["class"]]})
            else:
                set_row(self.moves, i, [num, p.san, "…", "…", "", "", fmt_ms(p.think_ms), fmt_ms(p.clock_ms)])
        self.moves.scrollToBottom()
        self.curve.setData(xs, ys)
        summ = rec.summary()
        for color in ("white", "black"):
            s = summ[color]
            acc = f"{s['accuracy']:.1f}%" if s["accuracy"] is not None else "–"
            acpl = f"{s['acpl']:.0f}" if s["acpl"] is not None else "–"
            counts = " ".join(f"{k}:{v}" for k, v in sorted(s["counts"].items()))
            self.info[color].setText(f"accuracy {acc} · ACPL {acpl} · {counts}")


class TimePanel(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self.think = pg.PlotWidget(title="Think time per move (s)")
        self.think.addLegend()
        self.think_w = pg.BarGraphItem(x=[], height=[], width=0.4, brush="#e0e0e0", name="white")
        self.think_b = pg.BarGraphItem(x=[], height=[], width=0.4, brush="#5a5a5a", name="black")
        self.think.addItem(self.think_w)
        self.think.addItem(self.think_b)
        self.clock = pg.PlotWidget(title="Clock after move (s)")
        self.clock.addLegend()
        self.clock_w = self.clock.plot([], [], pen=pg.mkPen("#e0e0e0", width=2), name="white")
        self.clock_b = self.clock.plot([], [], pen=pg.mkPen("#7aa2f7", width=2), name="black")
        lay.addWidget(self.think)
        lay.addWidget(self.clock)

    def update_record(self, rec):
        if rec is None:
            return
        w = [(i // 2 + 1, p) for i, p in enumerate(rec.plies) if p.mover == chess.WHITE]
        b = [(i // 2 + 1, p) for i, p in enumerate(rec.plies) if p.mover == chess.BLACK]
        self.think_w.setOpts(x=[n - 0.2 for n, _ in w], height=[p.think_ms / 1000 for _, p in w])
        self.think_b.setOpts(x=[n + 0.2 for n, _ in b], height=[p.think_ms / 1000 for _, p in b])
        self.clock_w.setData([n for n, p in w if p.clock_ms is not None], [p.clock_ms / 1000 for _, p in w if p.clock_ms is not None])
        self.clock_b.setData([n for n, p in b if p.clock_ms is not None], [p.clock_ms / 1000 for _, p in b if p.clock_ms is not None])


class NetworkPanel(QWidget):
    WINDOW = 300  # samples kept in the graph

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self.plot = pg.PlotWidget(title="RTT (ms)")
        self.plot.addLegend()
        self.pc = self.plot.plot([], [], pen=pg.mkPen("#7aa2f7", width=2), name="PC ⇄ server")
        self.fr = self.plot.plot([], [], pen=pg.mkPen("#e0af68", width=2), name="server ⇄ friend")
        lay.addWidget(self.plot)
        form = QFormLayout()
        self.labels = {k: QLabel("–") for k in ("state", "rtt", "jitter", "loss", "friend", "friend_rtt", "frames")}
        for k, text in (("state", "Link state"), ("rtt", "RTT"), ("jitter", "Jitter"), ("loss", "Loss (60 s)"),
                        ("friend", "Friend"), ("friend_rtt", "Friend RTT"), ("frames", "Messages in")):
            form.addRow(text, self.labels[k])
        lay.addLayout(form)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(2000)
        lay.addWidget(QLabel("<b>Link history</b>"))
        lay.addWidget(self.log)
        self.t0 = time.monotonic()
        self.pc_data: list[tuple[float, float]] = []
        self.fr_data: list[tuple[float, float]] = []
        self.frames = 0
        self.last_state = None

    def link_stats(self, s: dict):
        now = time.monotonic() - self.t0
        if s["state"] != self.last_state:
            self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  link {s['state']}")
            self.last_state = s["state"]
        self.labels["state"].setText(s["state"])
        self.labels["rtt"].setText(f"{s['rtt_ms']:.1f} ms" if s["rtt_ms"] is not None else "–")
        self.labels["jitter"].setText(f"{s['jitter_ms']:.1f} ms")
        self.labels["loss"].setText(f"{s['loss']:.0%}")
        if s["rtt_ms"] is not None:
            self.pc_data = (self.pc_data + [(now, s["rtt_ms"])])[-self.WINDOW:]
            self.pc.setData(*zip(*self.pc_data))

    def friend_net(self, net):
        self.labels["friend"].setText("connected" if net.friend_connected else "not connected")
        if net.friend_rtt_ms is not None:
            self.labels["friend_rtt"].setText(f"{net.friend_rtt_ms:.0f} ms")
            self.fr_data = (self.fr_data + [(time.monotonic() - self.t0, net.friend_rtt_ms)])[-self.WINDOW:]
            self.fr.setData(*zip(*self.fr_data))

    def frame(self):
        self.frames += 1
        self.labels["frames"].setText(str(self.frames))

    def note(self, text: str):
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")
