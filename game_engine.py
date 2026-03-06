import chess
import random
import math


class ChessGame:
    def __init__(self, fen=None):
        self.board = chess.Board(fen) if fen else chess.Board()

    def make_move(self, uci_str):
        try:
            move = chess.Move.from_uci(uci_str)
            if move in self.board.legal_moves:
                san = self.board.san(move)
                self.board.push(move)
                return {'success': True, 'san': san, 'fen': self.board.fen()}
            return {'success': False, 'error': 'Illegal move'}
        except ValueError:
            return {'success': False, 'error': 'Invalid move format'}

    def get_legal_moves(self):
        return [m.uci() for m in self.board.legal_moves]

    def is_game_over(self):
        return self.board.is_game_over()

    def get_result(self):
        if self.board.is_checkmate():
            return 'checkmate'
        if self.board.is_stalemate():
            return 'stalemate'
        if self.board.is_insufficient_material():
            return 'insufficient_material'
        if self.board.can_claim_fifty_moves():
            return 'fifty_moves'
        return None


class BotEngine:
    """Chess bot with varying difficulty levels using minimax."""

    PIECE_VALUES = {
        chess.PAWN: 100,
        chess.KNIGHT: 320,
        chess.BISHOP: 330,
        chess.ROOK: 500,
        chess.QUEEN: 900,
        chess.KING: 20000,
    }

    # Piece-square tables for positional evaluation
    PAWN_TABLE = [
        0,  0,  0,  0,  0,  0,  0,  0,
        50, 50, 50, 50, 50, 50, 50, 50,
        10, 10, 20, 30, 30, 20, 10, 10,
        5,  5, 10, 25, 25, 10,  5,  5,
        0,  0,  0, 20, 20,  0,  0,  0,
        5, -5,-10,  0,  0,-10, -5,  5,
        5, 10, 10,-20,-20, 10, 10,  5,
        0,  0,  0,  0,  0,  0,  0,  0,
    ]

    KNIGHT_TABLE = [
        -50,-40,-30,-30,-30,-30,-40,-50,
        -40,-20,  0,  0,  0,  0,-20,-40,
        -30,  0, 10, 15, 15, 10,  0,-30,
        -30,  5, 15, 20, 20, 15,  5,-30,
        -30,  0, 15, 20, 20, 15,  0,-30,
        -30,  5, 10, 15, 15, 10,  5,-30,
        -40,-20,  0,  5,  5,  0,-20,-40,
        -50,-40,-30,-30,-30,-30,-40,-50,
    ]

    def __init__(self, difficulty='medium'):
        self.difficulty = difficulty
        self.depth_map = {'easy': 1, 'medium': 2, 'hard': 3}
        self.randomness = {'easy': 0.4, 'medium': 0.15, 'hard': 0.02}

    def get_move(self, board):
        if board.is_game_over():
            return None

        depth = self.depth_map.get(self.difficulty, 2)
        legal_moves = list(board.legal_moves)

        if not legal_moves:
            return None

        # Easy: sometimes make random moves
        if self.difficulty == 'easy' and random.random() < self.randomness['easy']:
            return random.choice(legal_moves)

        best_move = None
        best_score = -math.inf
        alpha = -math.inf
        beta = math.inf

        for move in legal_moves:
            board.push(move)
            score = -self._minimax(board, depth - 1, -beta, -alpha, not board.turn)
            # Add some randomness based on difficulty
            score += random.uniform(0, self.randomness.get(self.difficulty, 0.1)) * 100
            board.pop()

            if score > best_score:
                best_score = score
                best_move = move
            alpha = max(alpha, score)

        return best_move

    def _minimax(self, board, depth, alpha, beta, maximizing):
        if depth == 0 or board.is_game_over():
            return self._evaluate(board)

        best_score = -math.inf
        for move in board.legal_moves:
            board.push(move)
            score = -self._minimax(board, depth - 1, -beta, -alpha, not maximizing)
            board.pop()

            best_score = max(best_score, score)
            alpha = max(alpha, score)
            if alpha >= beta:
                break

        return best_score

    def _evaluate(self, board):
        if board.is_checkmate():
            return -20000 if board.turn else 20000
        if board.is_stalemate() or board.is_insufficient_material():
            return 0

        score = 0
        for square in chess.SQUARES:
            piece = board.piece_at(square)
            if piece is None:
                continue

            value = self.PIECE_VALUES.get(piece.piece_type, 0)

            # Add positional bonus
            if piece.piece_type == chess.PAWN:
                idx = square if piece.color == chess.WHITE else chess.square_mirror(square)
                value += self.PAWN_TABLE[idx]
            elif piece.piece_type == chess.KNIGHT:
                idx = square if piece.color == chess.WHITE else chess.square_mirror(square)
                value += self.KNIGHT_TABLE[idx]

            if piece.color == chess.WHITE:
                score += value
            else:
                score -= value

        # Mobility bonus
        score += len(list(board.legal_moves)) * 5

        return score if board.turn == chess.WHITE else -score
