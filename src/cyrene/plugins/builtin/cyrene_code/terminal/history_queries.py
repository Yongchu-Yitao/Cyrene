"""History query projection, executed on the existing persistence workers."""
from typing import Any
import base64
import sqlite3
from .history import plain_terminal_text


class TerminalHistoryQueries:
    def __init__(self, *, oldest_seq, ensure_index, read_history, history_timestamp):
        self._oldest_seq = oldest_seq
        self._ensure_index = ensure_index
        self._read_history = read_history
        self._history_timestamp = history_timestamp

    def _commands_query(
        self, connection: sqlite3.Connection, terminal_id: str,
        output_start_seq: int, next_seq: int,
    ) -> list[dict[str, Any]]:
        output_start_seq = self._oldest_seq(terminal_id, output_start_seq)
        self._ensure_index(
            connection, terminal_id, output_start_seq, next_seq
        )
        rows = connection.execute(
            """SELECT command_id, command_text, output_start_seq,
                      output_end_seq, exit_code, started_at, finished_at, running
                 FROM terminal_commands
                WHERE terminal_id = ? AND command_start_seq >= ?
                  AND output_start_seq < ?
                ORDER BY output_start_seq""",
            (terminal_id, output_start_seq, next_seq),
        ).fetchall()
        return [{
            "id": str(row["command_id"]),
            "command": str(row["command_text"]),
            "outputStartSeq": int(row["output_start_seq"]),
            "outputEndSeq": min(next_seq, int(row["output_end_seq"])),
            "exitCode": row["exit_code"],
            "startedAt": str(row["started_at"] or ""),
            "finishedAt": str(row["finished_at"] or ""),
            "running": bool(row["running"]),
        } for row in rows]

    def _search_query(
        self, connection: sqlite3.Connection, sessions: tuple[dict[str, Any], ...],
        needle: str, limit: int,
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for session in sessions:
            terminal_id = str(session["id"])
            oldest = self._oldest_seq(
                terminal_id, int(session["outputStartSeq"])
            )
            next_seq = int(session["nextSeq"])
            self._ensure_index(connection, terminal_id, oldest, next_seq)
            first_line_row = connection.execute(
                """SELECT MIN(line_number) FROM terminal_text_chunks
                    WHERE terminal_id = ? AND end_seq > ? AND start_seq < ?""",
                (terminal_id, oldest, next_seq),
            ).fetchone()
            first_line = int(first_line_row[0] or 1)
            rows = connection.execute(
                """SELECT line_number, text, created_at
                     FROM terminal_text_chunks
                    WHERE terminal_id = ? AND end_seq > ? AND start_seq < ?
                      AND instr(search_text, ?) > 0
                    ORDER BY line_number""",
                (terminal_id, oldest, next_seq, needle),
            ).fetchall()
            for row in rows:
                matches.append({
                    "terminalId": terminal_id,
                    "title": session["title"],
                    "line": int(row["line_number"]) - first_line + 1,
                    "text": str(row["text"]),
                    "createdAt": str(row["created_at"] or ""),
                })
                if len(matches) >= limit:
                    return matches
        return matches

    def execute(self, connection, operation, arguments):
        if operation == "commands":
            return self._commands_query(connection, *arguments)
        if operation == "command_output":
            terminal_id, command_id, output_start_seq, next_seq = arguments
            command = next((item for item in self._commands_query(
                connection, terminal_id, output_start_seq, next_seq
            ) if item["id"] == command_id), None)
            if command is None:
                raise LookupError("terminal command not found")
            _oldest, _actual, data = self._read_history(
                terminal_id, int(command["outputStartSeq"]),
                int(command["outputEndSeq"]), output_start_seq,
            )
            return command, data, plain_terminal_text(data)
        if operation == "search":
            return self._search_query(connection, *arguments)
        if operation == "replay":
            terminal_id, cursor, target, chunk_size, output_start_seq = arguments
            oldest = self._oldest_seq(terminal_id, output_start_seq)
            position = max(oldest, min(target, cursor))
            events: list[dict[str, Any]] = []
            while position < target:
                _oldest, actual, data = self._read_history(
                    terminal_id, position, min(target, position + chunk_size), oldest
                )
                if not data:
                    break
                end = actual + len(data)
                events.append({
                    "type": "output",
                    "seq": actual,
                    "nextSeq": end,
                    "createdAt": self._history_timestamp(
                        connection, terminal_id, actual
                    ),
                    "data": base64.b64encode(data).decode("ascii"),
                })
                position = end
            return events
        raise ValueError(f"unknown terminal worker operation: {operation}")
