"""Worker-owned terminal parser and thread-safe screen snapshots."""
import codecs
from typing import Any
import pyte
from .shell_integration import OscMetadataParser


class TerminalScreenProjection:
    def __init__(self, condition):
        self._condition = condition
        self._screens = {}
        self._metadata_parsers = {}
        self._screen_snapshots = {}
        self.screen_bytes_parsed = 0

    def _screen_state(self, terminal_id: str, cols: int, rows: int):
        state = self._screens.get(terminal_id)
        if state is None:
            screen = pyte.Screen(cols, rows)
            state = (
                screen,
                pyte.Stream(screen),
                codecs.getincrementaldecoder("utf-8")(errors="replace"),
            )
            self._screens[terminal_id] = state
        return state

    def _feed_worker_screen(self, update: Any) -> None:
        screen, stream, decoder = self._screen_state(
            update.terminal_id, update.cols, update.rows
        )
        stream.feed(decoder.decode(update.data, final=False))
        self.screen_bytes_parsed += len(update.data)
        parser = self._metadata_parsers.setdefault(
            update.terminal_id, OscMetadataParser()
        )
        metadata = parser.feed(update.data, start_seq=update.start_seq)
        if metadata and update.metadata_loop is not None:
            update.metadata_loop.call_soon_threadsafe(
                update.metadata_callback, update.terminal_id, tuple(metadata)
            )
        snapshot = self._screen_body((screen, stream, decoder))
        with self._condition:
            self._screen_snapshots[update.terminal_id] = (
                update.next_seq, snapshot
            )

    @staticmethod
    def _screen_body(state: tuple[Any, Any, Any]) -> dict[str, Any]:
        screen = state[0]
        lines = [str(line).rstrip() for line in screen.display]
        while lines and not lines[-1]:
            lines.pop()
        return {
            "rows": int(screen.lines),
            "cols": int(screen.columns),
            "cursor": {
                "x": int(screen.cursor.x),
                "y": int(screen.cursor.y),
                "visible": not bool(getattr(screen.cursor, "hidden", False)),
            },
            "screenText": "\n".join(lines),
        }

    def cached_screen(
        self, terminal_id: str, minimum_seq: int,
    ) -> dict[str, Any] | None:
        with self._condition:
            entry = self._screen_snapshots.get(terminal_id)
            if entry is None or entry[0] < minimum_seq:
                return None
            return dict(entry[1])

    def read(self, terminal_id, cols, rows, output_start_seq, next_seq, *, read_history):
        state = self._screens.get(terminal_id)
        if state is None:
            state = self._screen_state(terminal_id, cols, rows)
            _oldest, _start, data = read_history(
                terminal_id, output_start_seq, next_seq, output_start_seq
            )
            if data:
                state[1].feed(state[2].decode(data, final=False))
        body = self._screen_body(state)
        with self._condition:
            self._screen_snapshots[terminal_id] = (next_seq, body)
        return body

    def resize(self, item):
        state = self._screens.get(item.terminal_id)
        if state is not None:
            state[0].resize(lines=item.rows, columns=item.cols)
            with self._condition:
                previous = self._screen_snapshots.get(
                    item.terminal_id, (0, {})
                )
                self._screen_snapshots[item.terminal_id] = (
                    previous[0], self._screen_body(state)
                )

    def reset_metadata(self, terminal_id):
        self._metadata_parsers.pop(terminal_id, None)

    def remove(self, terminal_id):
        self._screens.pop(terminal_id, None)
        self._metadata_parsers.pop(terminal_id, None)
        with self._condition:
            self._screen_snapshots.pop(terminal_id, None)
