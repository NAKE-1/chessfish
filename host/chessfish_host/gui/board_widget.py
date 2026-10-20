import chess
import chess.svg
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from ..stats.metrics import win_pct

LIGHT, DARK = QColor("#f0d9b5"), QColor("#b58863")
LAST = QColor(155, 199, 0, 105)
SELECT = QColor(20, 85, 30, 120)
ARROW_COLORS = [QColor(21, 120, 27, 200), QColor(0, 48, 136, 160), QColor(136, 32, 32, 140)]


class BoardWidget(QWidget):
    move_made = Signal(str)  # uci

    def __init__(self, parent=None):
        super().__init__(parent)
        self.board = chess.Board()
        self.orientation = chess.WHITE
        self.movable: chess.Color | None = None
        self.selected: int | None = None
        self.drag_pos: QPointF | None = None
        self.arrows: list[tuple[str, str, int]] = []  # (from, to, rank 0..2)
        self._svgs = {p: QSvgRenderer(chess.svg.piece(chess.Piece.from_symbol(p)).encode()) for p in "PNBRQKpnbrqk"}
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    # ---------- API ----------
    def set_board(self, board: chess.Board):
        self.board = board.copy()
        self.selected = None
        self.update()

    def set_orientation(self, color: chess.Color):
        self.orientation = color
        self.update()

    def set_movable(self, color: chess.Color | None):
        self.movable = color
        if color is None:
            self.selected = None
        self.update()

    def set_arrows(self, arrows):
        self.arrows = arrows
        self.update()

    # ---------- geometry ----------
    def _geom(self):
        side = min(self.width(), self.height())
        return (self.width() - side) / 2, (self.height() - side) / 2, side / 8

    def _sq_rect(self, sq: int) -> QRectF:
        x0, y0, s = self._geom()
        f, r = chess.square_file(sq), chess.square_rank(sq)
        if self.orientation == chess.WHITE:
            col, row = f, 7 - r
        else:
            col, row = 7 - f, r
        return QRectF(x0 + col * s, y0 + row * s, s, s)

    def _sq_at(self, p: QPointF) -> int | None:
        x0, y0, s = self._geom()
        col, row = int((p.x() - x0) // s), int((p.y() - y0) // s)
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        return chess.square(col, 7 - row) if self.orientation == chess.WHITE else chess.square(7 - col, row)

    # ---------- paint ----------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        _, _, s = self._geom()
        for sq in chess.SQUARES:
            light = (chess.square_file(sq) + chess.square_rank(sq)) % 2 == 1
            p.fillRect(self._sq_rect(sq), LIGHT if light else DARK)
        if self.board.move_stack:
            m = self.board.peek()
            for sq in (m.from_square, m.to_square):
                p.fillRect(self._sq_rect(sq), LAST)
        if self.selected is not None:
            p.fillRect(self._sq_rect(self.selected), SELECT)
        if self.board.is_check():
            k = self.board.king(self.board.turn)
            r = self._sq_rect(k)
            p.setBrush(QColor(255, 0, 0, 150))
            p.setPen(Qt.NoPen)
            p.drawEllipse(r.adjusted(s * .05, s * .05, -s * .05, -s * .05))
        # coordinates
        p.setFont(QFont("Segoe UI", max(7, int(s / 7))))
        for i in range(8):
            fsq = chess.square(i, 0 if self.orientation == chess.WHITE else 7)
            r = self._sq_rect(fsq)
            p.setPen(DARK if (i % 2 == 1) == (self.orientation == chess.WHITE) else LIGHT)
            p.drawText(r.adjusted(0, 0, -3, -1), Qt.AlignRight | Qt.AlignBottom, chess.FILE_NAMES[i])
            rsq = chess.square(0 if self.orientation == chess.WHITE else 7, i)
            r = self._sq_rect(rsq)
            p.setPen(DARK if (i % 2 == 1) == (self.orientation == chess.WHITE) else LIGHT)
            p.drawText(r.adjusted(3, 1, 0, 0), Qt.AlignLeft | Qt.AlignTop, chess.RANK_NAMES[i])
        # pieces
        for sq, piece in self.board.piece_map().items():
            if sq == self.selected and self.drag_pos is not None:
                continue
            self._svgs[piece.symbol()].render(p, self._sq_rect(sq))
        # legal dots
        if self.selected is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 85, 30, 110))
            for m in self.board.legal_moves:
                if m.from_square == self.selected:
                    r = self._sq_rect(m.to_square)
                    if self.board.piece_at(m.to_square):
                        p.setBrush(Qt.NoBrush)
                        p.setPen(QPen(QColor(20, 85, 30, 110), s * .08))
                        p.drawEllipse(r.adjusted(s * .06, s * .06, -s * .06, -s * .06))
                        p.setPen(Qt.NoPen)
                        p.setBrush(QColor(20, 85, 30, 110))
                    else:
                        p.drawEllipse(r.center(), s * .15, s * .15)
        for a, b, rank in reversed(self.arrows):
            self._arrow(p, a, b, rank, s)
        if self.selected is not None and self.drag_pos is not None:
            piece = self.board.piece_at(self.selected)
            if piece:
                self._svgs[piece.symbol()].render(p, QRectF(self.drag_pos.x() - s / 2, self.drag_pos.y() - s / 2, s, s))

    def _arrow(self, p: QPainter, a: str, b: str, rank: int, s: float):
        start = self._sq_rect(chess.parse_square(a)).center()
        end = self._sq_rect(chess.parse_square(b)).center()
        line = end - start
        length = (line.x() ** 2 + line.y() ** 2) ** .5
        if length == 0:
            return
        ux, uy = line.x() / length, line.y() / length
        width = s * (0.18 - rank * 0.04)
        head = s * 0.4
        tip = end - QPointF(ux, uy) * (s * 0.15)
        base = tip - QPointF(ux, uy) * head
        nx, ny = -uy, ux
        path = QPainterPath()
        path.moveTo(start + QPointF(nx, ny) * width / 2)
        path.lineTo(base + QPointF(nx, ny) * width / 2)
        path.lineTo(base + QPointF(nx, ny) * head / 2)
        path.lineTo(tip)
        path.lineTo(base - QPointF(nx, ny) * head / 2)
        path.lineTo(base - QPointF(nx, ny) * width / 2)
        path.lineTo(start - QPointF(nx, ny) * width / 2)
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(ARROW_COLORS[min(rank, 2)])
        p.drawPath(path)

    # ---------- input ----------
    def _can_move_from(self, sq):
        piece = self.board.piece_at(sq)
        return self.movable is not None and piece and piece.color == self.movable == self.board.turn

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        sq = self._sq_at(e.position())
        if sq is None:
            return
        if self.selected is not None and sq != self.selected and self._try_move(self.selected, sq):
            return
        if self._can_move_from(sq):
            self.selected, self.drag_pos = sq, e.position()
        else:
            self.selected = None
        self.update()

    def mouseMoveEvent(self, e):
        if self.drag_pos is not None:
            self.drag_pos = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        if self.drag_pos is None:
            return
        self.drag_pos = None
        sq = self._sq_at(e.position())
        if sq is not None and self.selected is not None and sq != self.selected:
            self._try_move(self.selected, sq)
        self.update()

    def _try_move(self, frm: int, to: int) -> bool:
        cands = [m for m in self.board.legal_moves if m.from_square == frm and m.to_square == to]
        if not cands:
            return False
        move = cands[0]
        if len(cands) > 1:  # promotion
            menu = QMenu(self)
            for pt, label in ((chess.QUEEN, "Queen"), (chess.ROOK, "Rook"), (chess.BISHOP, "Bishop"), (chess.KNIGHT, "Knight")):
                menu.addAction(label).setData(pt)
            act = menu.exec(self.mapToGlobal(self._sq_rect(to).center().toPoint()))
            if act is None:
                self.selected = None
                self.update()
                return True
            move = chess.Move(frm, to, promotion=act.data())
        self.selected = None
        self.board.push(move)  # optimistic; the server's STATE will confirm or snap back
        self.movable = None
        self.update()
        self.move_made.emit(move.uci())
        return True


class EvalBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cp, self.mate, self.orientation = 0, None, chess.WHITE
        self.setFixedWidth(28)

    def set_eval(self, cp: int, mate: int | None):
        self.cp, self.mate = cp, mate
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        h = self.height()
        white = win_pct(self.cp) / 100
        wh = h * white
        top_white = self.orientation == chess.BLACK
        p.fillRect(0, 0, self.width(), h, QColor("#222"))
        p.fillRect(QRectF(0, 0 if top_white else h - wh, self.width(), wh), QColor("#eee"))
        p.setFont(QFont("Segoe UI", 8, QFont.Bold))
        text = f"M{abs(self.mate)}" if self.mate else f"{abs(self.cp) / 100:.1f}"
        winning_white = self.cp >= 0
        at_bottom = winning_white != top_white
        p.setPen(QColor("#222") if winning_white else QColor("#eee"))
        p.drawText(QRectF(0, h - 20 if at_bottom else 2, self.width(), 18), Qt.AlignCenter, text)
