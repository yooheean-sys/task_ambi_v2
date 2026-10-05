"""LLM HTTP の前後フック（monitor-F01〜F05）。契約は contracts/hook-contract.md。

`HttpHook` は `LLMHttpHook`（IF-40〜43）を実装し、**上限判定による `RunHalted` 以外の例外を
呼び手へ飛ばさない**。監視のせいで本体が落ちるのは、監視を入れないより悪い（FR-027）。

**`httpx` は `TYPE_CHECKING` の中だけで import する**（FR-008・data-model.md §5）。実行時に
束縛しなければ `httpx.Client()` を書くこと自体が `NameError` になり、通信しないことが
機械的に守られる。

**スレッド安全**: `threading.Lock` を 1 本だけ持ち、`_state` の差し替え・`_unattributed` の加算・
`RunState` の全フィールドの更新・**採番と判定結果の決定**を守る。本文の読取と JSON 解釈・
トークン推定・ログ出力・`RunHalted` の送出は**ロックの外**に置く（hook-contract.md §9）。
"""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from common.interfaces.monitor import FailureKind, Measurement, RunHalted
from common.kernel.log import CorrelationContext, LoggerFactory
from monitor.models import RawFailure

if TYPE_CHECKING:
    import httpx

    from monitor import MonitorConfig
    from monitor.models import RunState

__all__ = ["HttpHook", "TokenEstimator"]

_LOGGER = LoggerFactory.get_logger("monitor.repository")

CHARS_PER_TOKEN = 4
"""`chars` 方式の割り算の分母（`ceil(len(連結 content) / 4)`＝ FR-029）。"""

_SSE_CONTENT_TYPE = "text/event-stream"
"""本文を読まない応答の種別（FR-023）。"""


def _now() -> datetime:
    """UTC の現在時刻（`RawFailure.at` に載せる）。"""
    return datetime.now(UTC)


def _header_names(headers: object) -> list[str]:
    """ヘッダの**名前だけ**を小文字で並べる（**値は載せない**＝ FR-024・FR-047）。"""
    keys = getattr(headers, "keys", None)
    if keys is None:
        return []
    return sorted(str(name).lower() for name in keys())


def _field(node: object, key: str) -> object:
    """`json.loads` の戻り（キーの型が静的に不明な dict）から 1 欄を読む。

    board の現物（`board/models.py:96-98`）と同じ扱いで、キーを文字列に揃えてから引く。
    """
    if not isinstance(node, dict):
        return None
    entry = cast("dict[object, object]", node)
    for name, value in entry.items():
        if str(name) == key:
            return value
    return None


def _content_text(content: object) -> str:
    """1 通のメッセージの `content` から照合・計数に使う文字列を取り出す。

    `str` はそのまま、`list`（マルチモーダル）は `str` 要素と `{"type":"text","text":...}` の
    `text` だけを拾う。それ以外の要素は**長さ 0** として扱う（hook-contract.md §4）。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for element in cast("list[object]", content):
            if isinstance(element, str):
                parts.append(element)
            elif _field(element, "type") == "text":
                text = _field(element, "text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _messages_of(payload: object) -> list[object]:
    """要求の本文から `messages` を取り出す。

    Raises:
        TypeError: 本文が JSON オブジェクトでない／`messages` が無い／`list` でない
            （呼び手はいずれも「推定不能」として 0 に倒し警告する）。
    """
    messages = _field(payload, "messages")
    if not isinstance(messages, list):
        msg = "要求の本文から messages を取り出せない"
        raise TypeError(msg)
    return list(cast("list[object]", messages))


def _last_content(payload: object) -> str | None:
    """`messages[-1].content` を照合用に取り出す（取り出せなければ `None`）。"""
    try:
        messages = _messages_of(payload)
    except TypeError:
        return None
    if not messages:
        return None
    return _content_text(_field(messages[-1], "content"))


def _walk(payload: object, path: str) -> object:
    """`.` 区切りの経路で JSON を辿る（辿れなければ `None`）。"""
    node: object = payload
    for key in path.split("."):
        node = _field(node, key)
        if node is None:
            return None
    return node


def _as_number(value: object) -> float | None:
    """`bool` を先に弾き、`int`／`float` だけを数値と認める（P-05）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_count(value: object) -> int:
    """`usage` の整数欄を読む（`bool` と非数値は 0 として扱う）。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


class TokenEstimator:
    """送信前トークン推定（monitor-F05・hook-contract.md §4）。"""

    def __init__(self, estimator: str, encoding: str) -> None:
        """方式（`chars`／`tiktoken`）と符号化名を控える。"""
        self._estimator = estimator
        self._encoding = encoding

    def estimate(self, payload: object) -> int:
        """要求の本文（解釈済み）から送信前トークン数を推定する。

        Args:
            payload: `json.loads(request.content)` の結果。

        Returns:
            推定したトークン数。

        Raises:
            TypeError: `messages` を取り出せない（呼び手が 0 として記録し警告する）。
        """
        joined = "".join(
            _content_text(_field(message, "content")) for message in _messages_of(payload)
        )
        if self._estimator == "tiktoken":
            return self._by_tiktoken(joined)
        return math.ceil(len(joined) / CHARS_PER_TOKEN)

    def _by_tiktoken(self, text: str) -> int:
        """`tiktoken` で符号化して長さを取る（**遅延 import**＝ research R-10）。"""
        # `tiktoken` は本 feature の裁定で依存に加えない（SD-14）ため未導入が既定であり、
        # 型スタブも解決できない。使用は `build()` の起動時検査（(d)）が導入済みを確かめた
        # ときだけに限られる。モジュール先頭に書くと monitor が tiktoken に静的依存する（R-10）。
        import tiktoken  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

        encoding = cast("object", tiktoken.get_encoding(self._encoding))  # pyright: ignore[reportUnknownMemberType]  # スタブ不在
        tokens = cast("list[int]", encoding.encode(text))  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]  # 同上
        return len(tokens)


class HttpHook:
    """`LLMHttpHook` の実装（monitor-F01〜F05）。"""

    def __init__(self, config: MonitorConfig) -> None:
        """推定器・費用の経路・照合する文言を設定から組む。"""
        self._estimator = TokenEstimator(config.token_estimator, config.token_encoding)
        self._cost_field_path = config.cost_field_path
        self._phrases = frozenset(phrase.strip() for phrase in config.truncation_phrases)
        self._lock = threading.Lock()
        self._state: RunState | None = None
        self._unattributed = 0

    # -- 帰属の付け外し ------------------------------------------------------------------

    def attach(self, state: RunState) -> RunState | None:
        """以後の呼び出しをこの kickoff に帰属させ、**打ち切った旧い器**を返す。"""
        with self._lock:
            previous = self._state
            self._state = state
        return previous

    def detach(self, kickoff_id: str) -> tuple[Measurement, list[RawFailure]]:
        """計測を締めて確定値と積まれた失敗を返す。

        `_unattributed` は**汲み出して 0 に戻し** `Measurement.unattributed_requests` に載せる。
        `begin` が無かった場合は `requests=0` の計測を作る（FR-036）。`kickoff_id` が `begin` 時と
        食い違えば**引数を正**とし警告する。

        Args:
            kickoff_id: 締める kickoff の識別子（`finish` の引数＝正）。

        Returns:
            確定した計測と、Hook が積んだ失敗の列。
        """
        finished_at = _now()
        with self._lock:
            state = self._state
            self._state = None
            unattributed = self._unattributed
            self._unattributed = 0
            measurement = _measure(state, kickoff_id, unattributed, finished_at)
            failures = [] if state is None else list(state.failures)
            begun_kickoff_id = None if state is None else state.kickoff_id
        if begun_kickoff_id is not None and begun_kickoff_id != kickoff_id:
            _LOGGER.warning(
                "monitor.hook.kickoff_mismatch",
                extra={"kickoff_id": kickoff_id, "begun_kickoff_id": begun_kickoff_id},
            )
        return measurement, failures

    # -- 送信前（IF-40） -----------------------------------------------------------------

    def on_outbound(self, request: httpx.Request) -> httpx.Request:
        """送信前にトークン・回数・コストを判定する（IF-40。hook-contract.md §2 の 8 手順）。

        Args:
            request: 送信しようとしている要求。

        Returns:
            受け取った要求（**書き換えない**）。

        Raises:
            RunHalted: 上限を超過した（**要求は送られない**）。
        """
        state = self._attached()  # (1) 帰属
        if state is None:
            self._count_unattributed()
            return request
        self._check_correlation(state)  # (2) 相関の照合（止めない）

        tokens = 0
        payload: object = None
        try:  # (3) 推定 —— 失敗は 0 として記録し警告する（FR-030）
            payload = json.loads(request.content)
            tokens = self._estimator.estimate(payload)
        except Exception as exc:  # noqa: BLE001  # 推定器の欠陥で本体を止めない（FR-030）
            _LOGGER.warning(
                "monitor.hook.estimate_failed",
                extra={"kickoff_id": state.kickoff_id, "reason": type(exc).__name__},
            )
        try:  # (4) 照合用の最終要素
            last_content = _last_content(payload)
        except Exception as exc:  # noqa: BLE001  # 計測欠落として記録し通す（FR-027）
            last_content = None
            self._warn_measure_failed(state, request.headers, exc)

        halt = self._reserve_and_judge(state, tokens)  # (5) ロック内で採番と判定
        if halt is not None:  # (6) ロックを出てから送出する
            kind, measured, limit, request_no = halt
            _LOGGER.warning(
                "monitor.hook.halted",
                extra={
                    "kickoff_id": state.kickoff_id,
                    "kind": kind.value,
                    "measured": measured,
                    "limit": limit,
                    "request_no": request_no,
                },
            )
            raise RunHalted(kind, measured, limit, request_no)

        try:  # (7) 文言照合（**止めない**）
            self._match_truncation(state, last_content)
        except Exception as exc:  # noqa: BLE001  # 計測欠落として記録し通す（FR-027）
            self._warn_measure_failed(state, request.headers, exc)
        return request  # (8) そのまま返す

    async def aon_outbound(self, request: httpx.Request) -> httpx.Request:
        """`on_outbound` の非同期版（IF-42）。

        要求側に非同期の I/O が無いので**同期版をそのまま呼ぶ**（hook-contract.md §8）。

        Args:
            request: 送信しようとしている要求。

        Returns:
            受け取った要求（**書き換えない**）。

        Raises:
            RunHalted: 上限を超過した。
        """
        return self.on_outbound(request)

    # -- 応答（IF-41・IF-43） -------------------------------------------------------------

    def on_inbound(self, response: httpx.Response) -> httpx.Response:
        """応答の usage・finish_reason を観測する（IF-41。hook-contract.md §5）。

        Args:
            response: 受け取った応答。

        Returns:
            受け取った応答（**書き換えない**）。
        """
        state = self._attached()  # (1) 帰属（**数えない**。要求側で数え済み）
        if state is None:
            _LOGGER.warning("monitor.hook.unattributed", extra={"unattributed_requests": 0})
            return response
        started = time.monotonic()
        if _is_stream(response):  # (2) SSE は本文を読まない
            return response
        try:  # (3)〜(7)
            response.read()
            _fill_elapsed(response, started)
            self._observe_payload(state, json.loads(response.content))
        except Exception as exc:  # noqa: BLE001  # 計測欠落として記録し通す（FR-027）
            self._warn_measure_failed(state, response.headers, exc)
        return response  # (8) そのまま返す

    async def aon_inbound(self, response: httpx.Response) -> httpx.Response:
        """`on_inbound` の非同期版（IF-43）。

        同期版と違うのは**本文の取り出し方だけ**で、観測は同じヘルパを通す（hook-contract.md §8）。

        Args:
            response: 受け取った応答。

        Returns:
            受け取った応答（**書き換えない**）。
        """
        state = self._attached()
        if state is None:
            _LOGGER.warning("monitor.hook.unattributed", extra={"unattributed_requests": 0})
            return response
        started = time.monotonic()
        if _is_stream(response):
            return response
        try:
            await response.aread()
            _fill_elapsed(response, started)
            self._observe_payload(state, json.loads(response.content))
        except Exception as exc:  # noqa: BLE001  # 計測欠落として記録し通す（FR-027）
            self._warn_measure_failed(state, response.headers, exc)
        return response

    # -- 内部 ----------------------------------------------------------------------------

    def _attached(self) -> RunState | None:
        """今どの kickoff に帰属するかを読む。"""
        with self._lock:
            return self._state

    def _count_unattributed(self) -> None:
        """帰属先の無い送信を数えて警告する（**止めない**＝ FR-035）。"""
        with self._lock:
            self._unattributed += 1
            total = self._unattributed
        _LOGGER.warning("monitor.hook.unattributed", extra={"unattributed_requests": total})

    def _check_correlation(self, state: RunState) -> None:
        """相関の識別子が `begin` 時と食い違えば警告する（**止めない**＝ FR-033）。"""
        current = CorrelationContext.current()
        if (
            current is not None
            and state.correlation_id is not None
            and current != state.correlation_id
        ):
            _LOGGER.warning(
                "monitor.hook.correlation_mismatch",
                extra={
                    "kickoff_id": state.kickoff_id,
                    "begun_correlation_id": state.correlation_id,
                },
            )

    def _warn_measure_failed(self, state: RunState, headers: object, exc: BaseException) -> None:
        """計測欠落を警告として残す（ヘッダは**名前だけ**）。"""
        _LOGGER.warning(
            "monitor.hook.measure_failed",
            extra={
                "kickoff_id": state.kickoff_id,
                "reason": type(exc).__name__,
                "header_names": _header_names(headers),
            },
        )

    def _reserve_and_judge(
        self, state: RunState, tokens: int
    ) -> tuple[FailureKind, float, float, int] | None:
        """採番して**長さ → 回数 → 費用**の順に厳密な超過を判定する（hook-contract.md §3）。

        判定結果の決定までを**ロック内**で行う。`RunHalted` の送出は呼び手がロックの外で行う。

        Args:
            state: 進行中の 1 kickoff。
            tokens: 推定した送信前トークン数（推定不能なら 0）。

        Returns:
            超過したときは `(種類, 実測値, 上限, 何回目)`。超過していなければ `None`。
        """
        at = _now()
        with self._lock:
            state.requests += 1
            request_no = state.requests
            state.max_tokens_before_send = max(state.max_tokens_before_send, tokens)
            limits = state.limits
            hit: tuple[FailureKind, float, float] | None = None
            if tokens > state.threshold_t:
                hit = (FailureKind.ctx_exceeded, tokens, state.threshold_t)
            elif request_no > limits.max_requests_per_kickoff:
                hit = (FailureKind.limit_requests, request_no, limits.max_requests_per_kickoff)
            elif state.cost_usd > limits.max_cost_per_kickoff:
                hit = (FailureKind.limit_cost, state.cost_usd, limits.max_cost_per_kickoff)
            if hit is None:
                return None
            kind, measured, limit = hit
            state.halted_reason = kind
            state.failures.append(
                RawFailure(
                    kind=kind,
                    evidence={"measured": measured, "limit": limit, "request_no": request_no},
                    at=at,
                )
            )
        return kind, measured, limit, request_no

    def _match_truncation(self, state: RunState, last_content: str | None) -> None:
        """強制最終回答の文言と**両辺 `strip()` して完全一致**なら積む（FR-040a・止めない）。"""
        if last_content is None or last_content.strip() not in self._phrases:
            return
        failure = RawFailure(
            kind=FailureKind.truncated_answer,
            evidence={"matched": "messages[-1]"},
            at=_now(),
        )
        with self._lock:
            state.failures.append(failure)

    def _observe_payload(self, state: RunState, payload: object) -> None:
        """応答の本文から計測を積む（hook-contract.md §5 の手順 4〜7）。

        `RunState` と**純データだけ**を引数に取るので、同期／非同期の双方から同じ形で呼べる。
        """
        at = _now()
        usage = _walk(payload, "usage")
        prompt = _as_count(_walk(payload, "usage.prompt_tokens"))
        completion = _as_count(_walk(payload, "usage.completion_tokens"))
        cost = _as_number(_walk(payload, self._cost_field_path))
        missing = usage is None or cost is None
        lengths = _finish_reasons(payload)
        with self._lock:
            state.prompt_tokens += prompt
            state.completion_tokens += completion
            if cost is not None:
                state.cost_usd += cost
            if missing:  # 応答 1 本につき最大 1（`usage` 欠落と `cost` 欠落を区別しない）
                state.cost_missing_count += 1
                state.failures.append(
                    RawFailure(
                        kind=FailureKind.usage_missing,
                        evidence={"cost_field_path": self._cost_field_path},
                        at=at,
                    )
                )
            if lengths:
                state.failures.append(
                    RawFailure(
                        kind=FailureKind.finish_length,
                        evidence={"finish_reason": "length"},
                        at=at,
                    )
                )


def _measure(
    state: RunState | None, kickoff_id: str, unattributed: int, finished_at: datetime
) -> Measurement:
    """`RunState` を確定値へ写す（`begin` が無かった場合は `requests=0` の計測を作る）。"""
    if state is None:
        return Measurement(
            kickoff_id=kickoff_id,
            requests=0,
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            cost_missing_count=0,
            max_tokens_before_send=0,
            halted_reason=None,
            started_at=finished_at,
            finished_at=finished_at,
            unattributed_requests=unattributed,
        )
    return Measurement(
        kickoff_id=kickoff_id,
        requests=state.requests,
        prompt_tokens=state.prompt_tokens,
        completion_tokens=state.completion_tokens,
        cost_usd=state.cost_usd,
        cost_missing_count=state.cost_missing_count,
        max_tokens_before_send=state.max_tokens_before_send,
        halted_reason=state.halted_reason,
        started_at=state.started_at,
        finished_at=finished_at,
        unattributed_requests=unattributed,
    )


def _finish_reasons(payload: object) -> bool:
    """`choices[*].finish_reason` に `"length"` があるか（FR-021）。"""
    choices = _walk(payload, "choices")
    if not isinstance(choices, list):
        return False
    return any(
        _field(choice, "finish_reason") == "length" for choice in cast("list[object]", choices)
    )


def _is_stream(response: httpx.Response) -> bool:
    """`content-type` の `;` より前を小文字化して SSE か判定する（FR-023）。"""
    content_type = response.headers.get("content-type", "")
    return content_type.split(";")[0].strip().lower() == _SSE_CONTENT_TYPE


def _fill_elapsed(response: httpx.Response, started: float) -> None:
    """読み取りの副作用を打ち消す（FR-025・hook-contract.md §6）。

    transport 層で `read()` すると `Response._content` がキャッシュされ、httpx が後段で行う
    `read()` が短絡して `BoundSyncStream.close()` が走らず `elapsed` が未設定のままになる。
    呼び手が `.elapsed` に触れると `RuntimeError` になるので、**httpx 自身と同じ公開セッタ**で
    埋める。

    getter は未設定のとき `RuntimeError` を投げるため `hasattr` で見る。
    `try: response.elapsed except RuntimeError:` でも同じことができるが、
    **例外を制御フローに使わない**ほうが読みやすいのでこちらを採る。
    **ストリームの内部状態（`_content`・`is_closed`・`_decoder`）には触らない**
    （触ると `content-encoding: gzip` の二重デコードなどを招く）。
    """
    if not hasattr(response, "_elapsed"):
        response.elapsed = timedelta(seconds=time.monotonic() - started)
